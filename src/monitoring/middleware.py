"""
Exception monitoring middleware.
Catches and reports unhandled exceptions to Telegram.
"""

import time
import hashlib
import traceback
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timedelta

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.monitoring.config import monitoring_config, AlertLevel
from app.monitoring.telegram import telegram_reporter
from app.core.queue.connection import get_redis_client


logger = logging.getLogger(__name__)


class ErrorDeduplicator:
    """
    Manages error deduplication to prevent spam.
    Uses Redis for distributed cache.
    """

    def __init__(self):
        self.local_cache: Dict[str, float] = {}  # Fallback for Redis failure
        self.rate_limit_minutes = monitoring_config.ALERT_RATE_LIMIT_MINUTES

    def generate_fingerprint(self, path: str, method: str, exception: Exception) -> str:
        """Generate unique fingerprint for error"""
        # Get first line of exception message
        error_msg = str(exception).split("\n")[0] if str(exception) else ""

        # Create fingerprint from key components
        key_parts = [
            path,
            method,
            type(exception).__name__,
            error_msg[:100],  # First 100 chars of error message
        ]

        # Create hash for consistent key
        key_str = "|".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()

    async def should_send_alert(self, fingerprint: str) -> bool:
        """
        Check if alert should be sent based on rate limiting.

        Returns:
            True if alert should be sent, False if rate limited
        """
        current_time = time.time()

        try:
            # Try Redis first
            redis_client = await get_redis_client()
            redis_key = monitoring_config.get_redis_key("error", fingerprint)

            # Check if key exists
            last_sent = await redis_client.get(redis_key)

            if last_sent:
                last_sent_time = float(last_sent)
                time_diff = current_time - last_sent_time

                if time_diff < (self.rate_limit_minutes * 60):
                    logger.debug(
                        f"Error {fingerprint} rate limited, last sent {time_diff:.1f}s ago"
                    )
                    return False

            # Set new timestamp with TTL
            ttl = self.rate_limit_minutes * 60 * 2  # Double the rate limit for TTL
            await redis_client.setex(redis_key, ttl, str(current_time))

            return True

        except Exception as e:
            logger.warning(
                f"Redis unavailable for deduplication: {e}, using local cache"
            )

            # Fallback to local cache
            if fingerprint in self.local_cache:
                last_sent_time = self.local_cache[fingerprint]
                time_diff = current_time - last_sent_time

                if time_diff < (self.rate_limit_minutes * 60):
                    return False

            self.local_cache[fingerprint] = current_time

            # Clean old entries from local cache (simple cleanup)
            if len(self.local_cache) > 1000:
                cutoff_time = current_time - (self.rate_limit_minutes * 60)
                self.local_cache = {
                    k: v for k, v in self.local_cache.items() if v > cutoff_time
                }

            return True

    async def record_error(self, path: str, status_code: int, exception_type: str):
        """Record error for statistics"""
        try:
            redis_client = await get_redis_client()

            # Increment daily error counter
            today = datetime.utcnow().strftime("%Y-%m-%d")

            # Total errors
            total_key = monitoring_config.get_redis_key("stats", today, "errors:total")
            await redis_client.incr(total_key)
            await redis_client.expire(total_key, 86400 * 7)  # Keep for 7 days

            # Errors by type
            type_key = monitoring_config.get_redis_key(
                "stats", today, f"errors:type:{exception_type}"
            )
            await redis_client.incr(type_key)
            await redis_client.expire(type_key, 86400 * 7)

            # Errors by endpoint
            endpoint_key = monitoring_config.get_redis_key(
                "stats", today, f"errors:endpoint:{path}"
            )
            await redis_client.incr(endpoint_key)
            await redis_client.expire(endpoint_key, 86400 * 7)

            # Errors by status code
            status_key = monitoring_config.get_redis_key(
                "stats", today, f"errors:status:{status_code}"
            )
            await redis_client.incr(status_key)
            await redis_client.expire(status_key, 86400 * 7)

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
                "email": getattr(request.state.user, "email", None),
            }

        try:
            # Process request
            response = await call_next(request)

            # Track slow requests (Phase 2)
            if monitoring_config.MONITOR_SLOW_REQUESTS:
                elapsed = time.time() - start_time
                if elapsed > monitoring_config.SLOW_REQUEST_THRESHOLD_SECONDS:
                    await self._report_slow_request(request_info, elapsed, user_info)

            return response

        except HTTPException as e:
            # HTTPExceptions are usually handled properly
            # Only report 500+ errors
            if e.status_code >= 500:
                await self._handle_exception(e, request_info, user_info, e.status_code)
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
                request_info["path"], request_info["method"], e
            )

            # Check rate limiting
            should_alert = await self.deduplicator.should_send_alert(fingerprint)

            if should_alert:
                await self._handle_exception(e, request_info, user_info, 500)

            # Record for statistics
            await self.deduplicator.record_error(
                request_info["path"], 500, exception_type
            )

            # Return generic error response
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "error_id": fingerprint},
            )

    async def _handle_exception(
        self,
        exception: Exception,
        request_info: Dict[str, Any],
        user_info: Optional[Dict[str, Any]],
        status_code: int,
    ):
        """Send exception alert to Telegram"""
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
                details["User"] = (
                    f"{user_info.get('email', 'unknown')} ({user_info.get('id', 'N/A')})"
                )
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
                traceback_str=tb_str,
            )

        except Exception as e:
            # Don't let monitoring errors break the application
            logger.error(f"Failed to send exception alert: {e}")

    async def _report_slow_request(
        self,
        request_info: Dict[str, Any],
        elapsed_time: float,
        user_info: Optional[Dict[str, Any]],
    ):
        """Report slow request"""
        try:
            # Generate fingerprint for deduplication (based on endpoint)
            fingerprint = f"slow:{request_info['path']}:{request_info['method']}"

            # Check if we should send alert (rate limiting)
            redis_client = await get_redis_client()
            slow_key = monitoring_config.get_redis_key("slow_requests", fingerprint)

            # Get current batch
            batch_key = monitoring_config.get_redis_key(
                "slow_requests_batch", datetime.utcnow().strftime("%Y-%m-%d-%H")
            )

            # Add to batch
            batch_data = {
                "path": f"{request_info['method']} {request_info['path']}",
                "time": elapsed_time,
                "user": (
                    user_info.get("email", "anonymous") if user_info else "anonymous"
                ),
                "timestamp": time.time(),
            }

            import json

            await redis_client.lpush(batch_key, json.dumps(batch_data))  # type: ignore
            await redis_client.expire(batch_key, 3600)

            # Check if we should send immediate alert (first occurrence)
            is_first = await redis_client.set(
                slow_key,
                "1",
                ex=monitoring_config.SLOW_REQUESTS_BATCH_MINUTES * 60,
                nx=True,  # Only set if doesn't exist
            )

            if is_first:
                # Send immediate alert for first slow request
                details = {
                    "Endpoint": f"{request_info['method']} {request_info['path']}",
                    "Response Time": f"{elapsed_time:.2f} seconds",
                    "Threshold": f"{monitoring_config.SLOW_REQUEST_THRESHOLD_SECONDS} seconds",
                }

                if user_info:
                    details["User"] = (
                        f"{user_info.get('email', 'unknown')} ({user_info.get('id', 'N/A')})"
                    )

                if request_info.get("query"):
                    details["Query"] = request_info["query"][:100]

                await telegram_reporter.send_alert(
                    title="Slow Request Detected",
                    message=f"Request took {elapsed_time:.1f}s to complete",
                    level=AlertLevel.WARNING,
                    details=details,
                )

            # Record statistics
            await self._record_slow_request_stats(request_info["path"], elapsed_time)

        except Exception as e:
            logger.error(f"Failed to report slow request: {e}")

    async def _record_slow_request_stats(self, path: str, elapsed_time: float):
        """Record slow request statistics"""
        try:
            redis_client = await get_redis_client()
            today = datetime.utcnow().strftime("%Y-%m-%d")

            # Increment counter
            count_key = monitoring_config.get_redis_key(
                "stats", today, f"slow_requests:{path}"
            )
            await redis_client.incr(count_key)
            await redis_client.expire(count_key, 86400 * 7)

            # Store response times
            times_key = monitoring_config.get_redis_key(
                "stats", today, "slow_requests:times"
            )
            await redis_client.lpush(times_key, f"{path}:{elapsed_time:.2f}")  # type: ignore
            await redis_client.ltrim(times_key, 0, 100)  # Keep last 100  # type: ignore
            await redis_client.expire(times_key, 86400 * 7)

        except Exception as e:
            logger.error(f"Failed to record slow request stats: {e}")
