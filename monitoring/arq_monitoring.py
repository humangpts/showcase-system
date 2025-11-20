"""
ARQ task monitoring wrapper.
Catches and reports failures in background tasks.

NOTE: This module requires ARQ to be installed:
    pip install fastapi-telemon[arq]
"""

import logging
import time
import traceback
import functools
from typing import Any, Callable, Dict, Optional
from datetime import datetime

try:
    from arq import cron as arq_cron
    ARQ_AVAILABLE = True
except ImportError:
    ARQ_AVAILABLE = False
    arq_cron = None

from monitoring.config import monitoring_config, AlertLevel
from monitoring.telegram import telegram_reporter
from monitoring import get_redis_adapter


logger = logging.getLogger(__name__)


def monitored_task(func: Callable) -> Callable:
    """
    Decorator for ARQ tasks to add monitoring capabilities.
    Wraps the task to catch exceptions and track execution time.
    
    Usage:
        @monitored_task
        async def my_task(ctx, ...):
            ...
    
    Note:
        Requires Redis adapter to be configured for statistics.
    """
    
    @functools.wraps(func)
    async def wrapper(ctx: Dict[str, Any], *args, **kwargs):
        """Wrapper that monitors task execution"""
        
        task_name = func.__name__
        
        # Skip monitoring if disabled or task is ignored
        if not monitoring_config.MONITOR_ARQ_TASKS:
            return await func(ctx, *args, **kwargs)
        
        if task_name in monitoring_config.ARQ_IGNORED_TASKS:
            return await func(ctx, *args, **kwargs)
        
        start_time = time.time()
        error_occurred = False
        error_details = None
        
        try:
            # Execute the task
            result = await func(ctx, *args, **kwargs)
            
            # Record successful completion
            await _record_task_success(task_name, start_time)
            
            # Check if task was slow
            execution_time = time.time() - start_time
            if execution_time > monitoring_config.ARQ_TASK_SLOW_THRESHOLD_SECONDS:
                await _report_slow_task(task_name, execution_time, args, kwargs)
            
            # Mark job as completed for health checks
            if task_name not in ['check_system_health', 'send_daily_report']:
                await _mark_job_completed()
            
            return result
            
        except Exception as e:
            error_occurred = True
            error_details = {
                "error": e,
                "traceback": traceback.format_exc()
            }
            
            # Record failure
            await _record_task_failure(task_name, e, start_time)
            
            # Send alert if enabled
            if monitoring_config.ARQ_TASK_FAILURE_ALERT:
                await _report_task_failure(task_name, e, args, kwargs)
            
            # Re-raise the exception to maintain ARQ retry behavior
            raise
            
        finally:
            # Log task completion
            execution_time = time.time() - start_time
            if error_occurred:
                logger.error(
                    f"Task {task_name} failed after {execution_time:.2f}s: "
                    f"{error_details['error']}"  # type: ignore
                )
            else:
                logger.info(
                    f"Task {task_name} completed successfully in {execution_time:.2f}s"
                )
    
    return wrapper


async def _record_task_success(task_name: str, start_time: float):
    """Record successful task execution for statistics"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Increment success counter
        success_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:success:{task_name}"
        )
        await redis_adapter.incr(success_key)
        await redis_adapter.expire(success_key, 86400 * 7)  # Keep for 7 days
        
        # Update execution time stats
        execution_time = time.time() - start_time
        time_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:time:{task_name}"
        )
        await redis_adapter.lpush(time_key, str(execution_time))
        await redis_adapter.ltrim(time_key, 0, 100)  # Keep last 100 executions
        await redis_adapter.expire(time_key, 86400 * 7)
        
        # Update last success time
        last_success_key = monitoring_config.get_redis_key(
            "tasks", "last_success", task_name
        )
        await redis_adapter.setex(last_success_key, 3600, str(time.time()))
        
    except Exception as e:
        logger.error(f"Failed to record task success: {e}")


async def _record_task_failure(task_name: str, error: Exception, start_time: float):
    """Record task failure for statistics"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Increment failure counter
        failure_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:failure:{task_name}"
        )
        await redis_adapter.incr(failure_key)
        await redis_adapter.expire(failure_key, 86400 * 7)
        
        # Record error type
        error_type = type(error).__name__
        error_type_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:errors:{error_type}"
        )
        await redis_adapter.incr(error_type_key)
        await redis_adapter.expire(error_type_key, 86400 * 7)
        
        # Update last failure time and error
        last_failure_key = monitoring_config.get_redis_key(
            "tasks", "last_failure", task_name
        )
        failure_data = {
            "time": time.time(),
            "error": str(error)[:200],
            "type": error_type
        }
        
        import json
        await redis_adapter.setex(
            last_failure_key, 
            86400,  # Keep for 1 day
            json.dumps(failure_data)
        )
        
    except Exception as e:
        logger.error(f"Failed to record task failure: {e}")


async def _report_task_failure(
    task_name: str,
    error: Exception,
    args: tuple,
    kwargs: dict
):
    """Send alert about task failure"""
    redis_adapter = get_redis_adapter()
    
    try:
        # Prepare task arguments for display (limit size)
        args_str = str(args)[:200] if args else "None"
        kwargs_str = str(kwargs)[:200] if kwargs else "None"
        
        # Get traceback
        tb_str = traceback.format_exc()
        
        # Prepare details
        details = {
            "Task": task_name,
            "Args": args_str,
            "Kwargs": kwargs_str,
        }
        
        # Check if this is a recurring failure
        if redis_adapter:
            failure_count_key = monitoring_config.get_redis_key(
                "tasks", "failure_count", task_name
            )
            failure_count = await redis_adapter.incr(failure_count_key)
            await redis_adapter.expire(failure_count_key, 3600)  # Reset every hour
            
            if failure_count > 1:
                details["Failure Count"] = f"{failure_count} in last hour"
        else:
            failure_count = 1
        
        # Send alert
        await telegram_reporter.send_alert(
            title="Background Task Failed",
            message=f"Task '{task_name}' failed to execute",
            level=AlertLevel.CRITICAL if failure_count > 3 else AlertLevel.WARNING,
            details=details,
            error=error,
            traceback_str=tb_str
        )
        
    except Exception as e:
        logger.error(f"Failed to report task failure: {e}")


async def _report_slow_task(
    task_name: str,
    execution_time: float,
    args: tuple,
    kwargs: dict
):
    """Report slow task execution"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        # Use deduplication for slow task alerts
        slow_key = monitoring_config.get_redis_key("tasks", "slow", task_name)
        
        # Check if we already sent alert recently
        last_alert = await redis_adapter.get(slow_key)
        if last_alert:
            return  # Skip if already alerted
        
        # Set flag with TTL
        await redis_adapter.setex(slow_key, 3600, "1")  # Alert once per hour
        
        # Prepare details
        args_str = str(args)[:100] if args else "None"
        details = {
            "Task": task_name,
            "Execution Time": f"{execution_time:.2f} seconds",
            "Threshold": f"{monitoring_config.ARQ_TASK_SLOW_THRESHOLD_SECONDS} seconds",
            "Args": args_str,
        }
        
        # Send warning alert
        await telegram_reporter.send_alert(
            title="Slow Background Task",
            message=f"Task '{task_name}' took {execution_time:.1f}s to execute",
            level=AlertLevel.WARNING,
            details=details
        )
        
    except Exception as e:
        logger.error(f"Failed to report slow task: {e}")


async def _mark_job_completed():
    """Mark that a job was completed for health monitoring"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        key = monitoring_config.get_redis_key("queue", "last_job_completed")
        await redis_adapter.setex(key, 3600, str(time.time()))
    except Exception as e:
        logger.error(f"Failed to mark job completed: {e}")


def monitored_periodic_task(
    cron_spec: Optional[str] = None,
    **cron_kwargs
) -> Callable:
    """
    Decorator for periodic tasks with monitoring.
    
    Usage with ARQ cron:
        @monitored_periodic_task(hour={9}, minute={0})
        async def daily_task(ctx):
            ...
    
    Usage without ARQ (for other schedulers):
        @monitored_periodic_task()
        async def my_task(ctx):
            ...
    
    Args:
        cron_spec: Cron expression string (if supported by scheduler)
        **cron_kwargs: Cron parameters (hour, minute, etc.)
    
    Note:
        If ARQ is not installed, this will return a simple monitored wrapper.
    """
    def decorator(func: Callable) -> Callable:
        # Apply monitoring wrapper first
        monitored_func = monitored_task(func)
        
        # If ARQ is available and cron parameters provided, use ARQ's cron decorator
        if ARQ_AVAILABLE and arq_cron and (cron_spec or cron_kwargs):
            if cron_spec:
                # TODO: ARQ doesn't support cron strings directly
                # You might need custom parsing here
                logger.warning(
                    "Cron string specification not directly supported by ARQ. "
                    "Use hour={}, minute={}, etc. instead."
                )
                return monitored_func
            else:
                # Use ARQ's cron decorator
                return arq_cron(monitored_func, **cron_kwargs)
        
        # Otherwise just return the monitored function
        return monitored_func
    
    return decorator


# Backward compatibility
def task(*args, **kwargs):
    """
    Alias for monitored_task for backward compatibility.
    
    Usage:
        from monitoring.arq_monitoring import task
        
        @task
        async def my_background_task(ctx):
            ...
    """
    if len(args) == 1 and callable(args[0]):
        # Used as @task without parameters
        return monitored_task(args[0])
    else:
        # Used as @task(...) with parameters
        # In this case, we ignore parameters and just apply monitoring
        def decorator(func):
            return monitored_task(func)
        return decorator