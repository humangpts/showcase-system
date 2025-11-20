"""
Exception monitoring middleware.
Catches and reports unhandled exceptions to Telegram.
"""

import time
import hashlib
import traceback
import logging
import json
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from monitoring.config import monitoring_config, AlertLevel
from monitoring.telegram import telegram_reporter


logger = logging.getLogger(__name__)


class ErrorDeduplicator:
    """
    Manages error deduplication to prevent spam.
    Uses Redis for distributed cache when available.
    """
    
    def __init__(self):
        self.local_cache: Dict[str, float] = {}  # Fallback for Redis failure
        self.rate_limit_minutes = monitoring_config.ALERT_RATE_LIMIT_MINUTES
    
    def generate_fingerprint(
        self,
        path: str,
        method: str,
        exception: Exception
    ) -> str:
        """Generate unique fingerprint for error"""
        # Get first line of exception message
        error_msg = str(exception).split('\n')[0] if str(exception) else ""
        
        # Create fingerprint from key components
        key_parts = [
            path,
            method,
            type(exception).__name__,
            error_msg[:100]  # First 100 chars of error message
        ]
        
        # Create hash for consistent key
        key_str = "|".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    async def should_send_alert(self, fingerprint: str) -> bool:
        """
        Check if alert should be sent based on rate limiting.
        Uses atomic Redis operations to prevent race conditions.
        
        Returns:
            True if alert should be sent, False if rate limited
        """
        current_time = time.time()
        
        # Try Redis first if available
        from monitoring import get_redis_adapter
        redis_adapter = get_redis_adapter()
        
        if redis_adapter:
            try:
                redis_key = monitoring_config.get_redis_key("error", fingerprint)
                ttl = self.rate_limit_minutes * 60
                
                # ATOMIC OPERATION: Try to set key only if it doesn't exist
                # This prevents race condition where multiple workers check at the same time
                was_set = await redis_adapter.set(
                    redis_key,
                    str(current_time),
                    ex=ttl,
                    nx=True  # Only set if key does NOT exist
                )
                
                if not was_set:
                    # Key already exists - we're rate limited
                    # Try to get the existing value to log how long ago it was sent
                    try:
                        last_sent = await redis_adapter.get(redis_key)
                        if last_sent:
                            last_sent_time = float(last_sent)
                            time_diff = current_time - last_sent_time
                            logger.debug(
                                f"Error {fingerprint} rate limited, "
                                f"last sent {time_diff:.1f}s ago"
                            )
                    except (ValueError, TypeError):
                        # If we can't parse the timestamp, just log that it's rate limited
                        logger.debug(f"Error {fingerprint} rate limited")
                    
                    return False
                
                # Key was successfully set - we can send alert
                logger.debug(f"Error {fingerprint} allowing alert (first or expired)")
                return True
                
            except Exception as e:
                logger.warning(
                    f"Redis unavailable for deduplication: {e}, "
                    "using local cache"
                )
                # Fall through to local cache
        
        # Fallback to local cache (not distributed, only works for single worker)
        if fingerprint in self.local_cache:
            last_sent_time = self.local_cache[fingerprint]
            time_diff = current_time - last_sent_time
            
            if time_diff < (self.rate_limit_minutes * 60):
                logger.debug(
                    f"Error {fingerprint} rate limited (local cache), "
                    f"last sent {time_diff:.1f}s ago"
                )
                return False
        
        # Not in cache or expired - allow alert
        self.local_cache[fingerprint] = current_time
        
        # Clean old entries from local cache to prevent memory leak
        if len(self.local_cache) > 1000:
            cutoff_time = current_time - (self.rate_limit_minutes * 60)
            self.local_cache = {
                k: v for k, v in self.local_cache.items()
                if v > cutoff_time
            }
        
        return True
    
    async def record_error(
        self,
        path: str,
        status_code: int,
        exception_type: str
    ):
        """Record error for statistics"""
        from monitoring import get_redis_adapter
        redis_adapter = get_redis_adapter()
        
        if not redis_adapter:
            return
        
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            
            # Total errors
            total_key = monitoring_config.get_redis_key("stats", today, "errors:total")
            await redis_adapter.incr(total_key)
            await redis_adapter.expire(total_key, 86400 * 7)  # Keep for 7 days
            
            # Errors by type
            type_key = monitoring_config.get_redis_key("stats", today, f"errors:type:{exception_type}")
            await redis_adapter.incr(type_key)
            await redis_adapter.expire(type_key, 86400 * 7)
            
            # Errors by endpoint
            endpoint_key = monitoring_config.get_redis_key("stats", today, f"errors:endpoint:{path}")
            await redis_adapter.incr(endpoint_key)
            await redis_adapter.expire(endpoint_key, 86400 * 7)
            
            # Errors by status code
            status_key = monitoring_config.get_redis_key("stats", today, f"errors:status:{status_code}")
            await redis_adapter.incr(status_key)
            await redis_adapter.expire(status_key, 86400 * 7)
            
        except Exception as e:
            logger.error(f"Failed to record error statistics: {e}")


class MonitoringMiddleware(BaseHTTPMiddleware):
    """
    Middleware for monitoring exceptions and performance.
    """
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.deduplicator = ErrorDeduplicator()
        self.enabled = monitoring_config.is_enabled
        self._background_tasks = set()  # Track background tasks
    
    async def dispatch(self, request: Request, call_next):
        """
        Process request and catch exceptions.
        """
        # Skip monitoring if disabled
        if not self.enabled:
            return await call_next(request)
        
        # Skip ignored paths
        if not monitoring_config.should_monitor_path(request.url.path):
            return await call_next(request)
        
        # Track request timing
        start_time = time.time()
        
        # Store request info for error reporting
        request_info = {
            "path": request.url.path,
            "method": request.method,
            "query": str(request.url.query) if request.url.query else None,
            "headers": dict(request.headers) if request.headers else {},
        }
        
        # Get user info if available
        user_info = None
        if hasattr(request.state, "user"):
            user_info = {
                "id": str(getattr(request.state.user, "id", "unknown")),
                "email": getattr(request.state.user, "email", None)
            }
        
        try:
            # Process request
            response = await call_next(request)
            
            # Track slow requests
            if monitoring_config.MONITOR_SLOW_REQUESTS:
                elapsed = time.time() - start_time
                if elapsed > monitoring_config.SLOW_REQUEST_THRESHOLD_SECONDS:
                    # Fire and forget - don't wait for alert
                    self._create_background_task(
                        self._report_slow_request(request_info, elapsed, user_info)
                    )
            
            return response
            
        except HTTPException as e:
            # HTTPExceptions are usually handled properly
            # Only report 500+ errors
            if e.status_code >= 500:
                # Fire and forget
                self._create_background_task(
                    self._handle_exception(e, request_info, user_info, e.status_code)
                )
            raise
            
        except Exception as e:
            # Unhandled exceptions - these are the real problems
            exception_type = type(e).__name__
            
            # Check if we should monitor this exception
            if not monitoring_config.should_monitor_exception(exception_type):
                logger.debug(f"Ignoring exception type: {exception_type}")
                raise
            
            # Generate fingerprint for deduplication
            fingerprint = self.deduplicator.generate_fingerprint(
                request_info["path"],
                request_info["method"],
                e
            )
            
            # Check rate limiting
            should_alert = await self.deduplicator.should_send_alert(fingerprint)
            
            if should_alert:
                # Fire and forget - don't block response
                self._create_background_task(
                    self._handle_exception(e, request_info, user_info, 500)
                )
            
            # Record for statistics (also fire and forget)
            self._create_background_task(
                self.deduplicator.record_error(
                    request_info["path"],
                    500,
                    exception_type
                )
            )
            
            # Return generic error response immediately
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "error_id": fingerprint
                }
            )
    
    def _create_background_task(self, coro):
        """
        Create background task with proper error handling and cleanup.
        
        Ensures that:
        1. Exceptions in background tasks are logged
        2. Tasks are properly cleaned up from the set
        3. Monitoring errors don't crash the application
        
        Args:
            coro: Coroutine to run in background
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        
        def _handle_task_result(t: asyncio.Task):
            """Handle task completion and any exceptions"""
            # Remove from set
            self._background_tasks.discard(t)
            
            # Check for exceptions
            try:
                # This will raise if task failed
                t.result()
            except asyncio.CancelledError:
                # Task was cancelled - this is fine
                logger.debug("Background monitoring task was cancelled")
            except Exception as e:
                # Log the error but don't crash the app
                logger.error(
                    f"Background monitoring task failed: {e}",
                    exc_info=True,
                    extra={
                        'task_name': t.get_name(),
                        'task_coro': str(coro)
                    }
                )
                
        
        task.add_done_callback(_handle_task_result)
        
        return task
    
    async def _handle_exception(
        self,
        exception: Exception,
        request_info: Dict[str, Any],
        user_info: Optional[Dict[str, Any]],
        status_code: int
    ):
        """Send exception alert to Telegram (fire and forget)"""
        try:
            # Get traceback
            tb_str = traceback.format_exc()
            
            # Prepare details
            details = {
                "Endpoint": f"{request_info['method']} {request_info['path']}",
                "Status": status_code,
            }
            
            if request_info.get("query"):
                details["Query"] = request_info["query"]
            
            if user_info:
                details["User"] = f"{user_info.get('email', 'unknown')} ({user_info.get('id', 'N/A')})"
            else:
                details["User"] = "Anonymous"
            
            # Add relevant headers
            headers = request_info.get("headers", {})
            if "user-agent" in headers:
                details["User-Agent"] = headers["user-agent"][:100]
            
            # Send alert
            await telegram_reporter.send_alert(
                title=f"ERROR {status_code}",
                message=f"Unhandled exception in {request_info['path']}",
                level=AlertLevel.CRITICAL,
                details=details,
                error=exception,
                traceback_str=tb_str
            )
            
        except Exception as e:
            # Don't let monitoring errors break the application
            logger.error(f"Failed to send exception alert: {e}")
    
    async def _report_slow_request(
        self,
        request_info: Dict[str, Any],
        elapsed_time: float,
        user_info: Optional[Dict[str, Any]]
    ):
        """Report slow request (fire and forget)"""
        from monitoring import get_redis_adapter
        redis_adapter = get_redis_adapter()
        
        if not redis_adapter:
            return
        
        try:
            # Generate fingerprint for deduplication
            fingerprint = f"slow:{request_info['path']}:{request_info['method']}"
            
            # Get current batch
            batch_key = monitoring_config.get_redis_key(
                "slow_requests_batch",
                datetime.utcnow().strftime("%Y-%m-%d-%H")
            )
            
            # Add to batch
            batch_data = {
                "path": f"{request_info['method']} {request_info['path']}",
                "time": elapsed_time,
                "user": user_info.get("email", "anonymous") if user_info else "anonymous",
                "timestamp": time.time()
            }
            
            await redis_adapter.lpush(batch_key, json.dumps(batch_data))
            await redis_adapter.expire(batch_key, 3600)
            
            # Check if we should send immediate alert (first occurrence)
            slow_key = monitoring_config.get_redis_key("slow_requests", fingerprint)
            is_first = await redis_adapter.set(
                slow_key, 
                "1", 
                ex=monitoring_config.SLOW_REQUESTS_BATCH_MINUTES * 60,
                nx=True
            )
            
            if is_first:
                # Send immediate alert for first slow request
                details = {
                    "Endpoint": f"{request_info['method']} {request_info['path']}",
                    "Response Time": f"{elapsed_time:.2f} seconds",
                    "Threshold": f"{monitoring_config.SLOW_REQUEST_THRESHOLD_SECONDS} seconds",
                }
                
                if user_info:
                    details["User"] = f"{user_info.get('email', 'unknown')} ({user_info.get('id', 'N/A')})"
                
                if request_info.get("query"):
                    details["Query"] = request_info["query"][:100]
                
                await telegram_reporter.send_alert(
                    title="Slow Request Detected",
                    message=f"Request took {elapsed_time:.1f}s to complete",
                    level=AlertLevel.WARNING,
                    details=details
                )
            
            # Record statistics
            await self._record_slow_request_stats(request_info['path'], elapsed_time, redis_adapter)
            
        except Exception as e:
            logger.error(f"Failed to report slow request: {e}")
    
    async def _record_slow_request_stats(self, path: str, elapsed_time: float, redis_adapter):
        """Record slow request statistics"""
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            
            # Increment counter
            count_key = monitoring_config.get_redis_key("stats", today, f"slow_requests:{path}")
            await redis_adapter.incr(count_key)
            await redis_adapter.expire(count_key, 86400 * 7)
            
            # Store response times
            times_key = monitoring_config.get_redis_key("stats", today, "slow_requests:times")
            await redis_adapter.lpush(times_key, f"{path}:{elapsed_time:.2f}")
            await redis_adapter.ltrim(times_key, 0, 100)  # Keep last 100
            await redis_adapter.expire(times_key, 86400 * 7)
            
        except Exception as e:
            logger.error(f"Failed to record slow request stats: {e}")