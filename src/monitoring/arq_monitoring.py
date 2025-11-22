"""
ARQ task monitoring wrapper.
Catches and reports failures in background tasks.
"""

import logging
import time
import traceback
import functools
from typing import Any, Callable, Dict
from datetime import datetime

from app.monitoring.config import monitoring_config, AlertLevel
from app.monitoring.telegram import telegram_reporter
from app.core.queue.connection import get_redis_client


logger = logging.getLogger(__name__)


def monitored_task(func: Callable) -> Callable:
    """
    Decorator for ARQ tasks to add monitoring capabilities.
    Wraps the task to catch exceptions and track execution time.

    Usage:
        @monitored_task
        @task
        async def my_task(ctx, ...):
            ...
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
            if task_name not in ["check_system_health", "send_daily_report"]:
                await _mark_job_completed()

            return result

        except Exception as e:
            error_occurred = True
            error_details = {"error": e, "traceback": traceback.format_exc()}

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
                    f"Task {task_name} failed after {execution_time:.2f}s: {error_details['error']}"  # type: ignore
                )
            else:
                logger.info(
                    f"Task {task_name} completed successfully in {execution_time:.2f}s"
                )

    return wrapper


async def _record_task_success(task_name: str, start_time: float):
    """Record successful task execution for statistics"""
    try:
        redis_client = await get_redis_client()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Increment success counter
        success_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:success:{task_name}"
        )
        await redis_client.incr(success_key)
        await redis_client.expire(success_key, 86400 * 7)  # Keep for 7 days

        # Update execution time stats
        execution_time = time.time() - start_time
        time_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:time:{task_name}"
        )
        await redis_client.lpush(time_key, str(execution_time))  # type: ignore
        await redis_client.ltrim(
            time_key, 0, 100
        )  # Keep last 100 executions # type: ignore
        await redis_client.expire(time_key, 86400 * 7)

        # Update last success time
        last_success_key = monitoring_config.get_redis_key(
            "tasks", "last_success", task_name
        )
        await redis_client.setex(last_success_key, 3600, str(time.time()))

    except Exception as e:
        logger.error(f"Failed to record task success: {e}")


async def _record_task_failure(task_name: str, error: Exception, start_time: float):
    """Record task failure for statistics"""
    try:
        redis_client = await get_redis_client()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Increment failure counter
        failure_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:failure:{task_name}"
        )
        await redis_client.incr(failure_key)
        await redis_client.expire(failure_key, 86400 * 7)

        # Record error type
        error_type = type(error).__name__
        error_type_key = monitoring_config.get_redis_key(
            "stats", today, f"tasks:errors:{error_type}"
        )
        await redis_client.incr(error_type_key)
        await redis_client.expire(error_type_key, 86400 * 7)

        # Update last failure time and error
        last_failure_key = monitoring_config.get_redis_key(
            "tasks", "last_failure", task_name
        )
        failure_data = {
            "time": time.time(),
            "error": str(error)[:200],
            "type": error_type,
        }

        import json

        await redis_client.setex(
            last_failure_key, 86400, json.dumps(failure_data)  # Keep for 1 day
        )

    except Exception as e:
        logger.error(f"Failed to record task failure: {e}")


async def _report_task_failure(
    task_name: str, error: Exception, args: tuple, kwargs: dict
):
    """Send alert about task failure"""
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
        redis_client = await get_redis_client()
        failure_count_key = monitoring_config.get_redis_key(
            "tasks", "failure_count", task_name
        )
        failure_count = await redis_client.incr(failure_count_key)
        await redis_client.expire(failure_count_key, 3600)  # Reset counter every hour

        if failure_count > 1:
            details["Failure Count"] = f"{failure_count} in last hour"

        # Send alert
        await telegram_reporter.send_alert(
            title="Background Task Failed",
            message=f"Task '{task_name}' failed to execute",
            level=AlertLevel.CRITICAL if failure_count > 3 else AlertLevel.WARNING,
            details=details,
            error=error,
            traceback_str=tb_str,
        )

    except Exception as e:
        logger.error(f"Failed to report task failure: {e}")


async def _report_slow_task(
    task_name: str, execution_time: float, args: tuple, kwargs: dict
):
    """Report slow task execution"""
    try:
        # Use deduplication for slow task alerts
        redis_client = await get_redis_client()
        slow_key = monitoring_config.get_redis_key("tasks", "slow", task_name)

        # Check if we already sent alert recently
        last_alert = await redis_client.get(slow_key)
        if last_alert:
            return  # Skip if already alerted

        # Set flag with TTL
        await redis_client.setex(slow_key, 3600, "1")  # Alert once per hour

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
            details=details,
        )

    except Exception as e:
        logger.error(f"Failed to report slow task: {e}")


async def _mark_job_completed():
    """Mark that a job was completed for health monitoring"""
    try:
        redis_client = await get_redis_client()
        key = monitoring_config.get_redis_key("queue", "last_job_completed")
        await redis_client.setex(key, 3600, str(time.time()))
    except Exception as e:
        logger.error(f"Failed to mark job completed: {e}")


# Export monitored versions of common task decorators
def monitored_periodic_task(func: Callable) -> Callable:
    """
    Decorator for periodic tasks with monitoring.

    Usage:
        @monitored_periodic_task
        async def my_periodic_task(ctx):
            ...
    """
    from app.core.queue.decorators import periodic_task

    @periodic_task
    @monitored_task
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)

    return wrapper
