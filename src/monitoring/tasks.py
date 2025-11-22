"""
Monitoring background tasks.
Health checks and daily reports.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import asyncio

# import psutil

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue.decorators import periodic_task
from app.core.queue.connection import get_redis_client
from app.core.database import async_session_maker
from app.core.datetime_utils import utc_now
from app.monitoring.config import monitoring_config, health_config, metrics_config
from app.monitoring.telegram import telegram_reporter, AlertLevel
from app.users.models import User
from app.projects.models import Project


logger = logging.getLogger(__name__)


@periodic_task
async def check_system_health(ctx: dict):
    """
    Periodic health check for all system components.
    Runs every N minutes as configured.
    """
    if not monitoring_config.is_enabled:
        return

    logger.info("Running system health check...")

    errors = []
    components = {}

    # 1. Check Database
    if health_config.check_database:
        components["Database"] = await _check_database_health(errors)

    # 2. Check Redis
    if health_config.check_redis:
        components["Redis"] = await _check_redis_health(errors)

    # 3. Check Disk Space
    # if health_config.check_disk:
    #     components["Disk"] = await _check_disk_health(errors)

    # 4. Check Memory
    # if health_config.check_memory:
    #     components["Memory"] = await _check_memory_health(errors)

    # 5. Check Queue (ARQ)
    if health_config.check_queue:
        components["Queue"] = await _check_queue_health(errors)

    # Send alert if any component is unhealthy
    if any(not healthy for healthy in components.values()):
        logger.warning(f"Health check failed: {errors}")
        await telegram_reporter.send_health_alert(components=components, errors=errors)

        # Store health status in Redis
        await _store_health_status(components, errors)
    else:
        logger.info("Health check passed: all systems operational")

        # Store success status
        await _store_health_status(components, [])


async def _check_database_health(errors: List[str]) -> bool:
    """Check if database is healthy"""
    try:
        async with async_session_maker() as session:
            # Set timeout for health check
            result = await asyncio.wait_for(
                session.execute(select(func.count()).select_from(User).limit(1)),
                timeout=monitoring_config.HEALTH_DB_TIMEOUT_SECONDS,
            )
            _ = result.scalar()
            return True

    except asyncio.TimeoutError:
        errors.append(
            f"Database query timeout (>{monitoring_config.HEALTH_DB_TIMEOUT_SECONDS}s)"
        )
        return False
    except Exception as e:
        errors.append(f"Database error: {str(e)[:100]}")
        return False


async def _check_redis_health(errors: List[str]) -> bool:
    """Check if Redis is healthy"""
    try:
        redis_client = await get_redis_client()

        # Test with ping and simple set/get
        await asyncio.wait_for(
            redis_client.ping(), timeout=monitoring_config.HEALTH_REDIS_TIMEOUT_SECONDS
        )

        # Test write/read
        test_key = monitoring_config.get_redis_key("health", "test")
        test_value = str(time.time())
        await redis_client.setex(test_key, 60, test_value)
        read_value = await redis_client.get(test_key)

        if read_value != test_value:
            errors.append("Redis read/write test failed")
            return False

        return True

    except asyncio.TimeoutError:
        errors.append(
            f"Redis timeout (>{monitoring_config.HEALTH_REDIS_TIMEOUT_SECONDS}s)"
        )
        return False
    except Exception as e:
        errors.append(f"Redis error: {str(e)[:100]}")
        return False


# async def _check_disk_health(errors: List[str]) -> bool:
#     """Check disk space usage"""
#     try:
#         disk_usage = psutil.disk_usage('/')
#         usage_percent = disk_usage.percent

#         # Check critical threshold
#         critical_threshold = health_config.disk_critical or monitoring_config.HEALTH_DISK_CRITICAL_PERCENT

#         if usage_percent >= critical_threshold:
#             errors.append(
#                 f"Disk usage critical: {usage_percent:.1f}% "
#                 f"({disk_usage.free // (1024**3)}GB free)"
#             )
#             return False

#         # Check warning threshold
#         warning_threshold = health_config.disk_warning or monitoring_config.HEALTH_DISK_WARNING_PERCENT

#         if usage_percent >= warning_threshold:
#             errors.append(
#                 f"Disk usage warning: {usage_percent:.1f}% "
#                 f"({disk_usage.free // (1024**3)}GB free)"
#             )
#             # Still return True for warning, but include in errors

#         return True

#     except Exception as e:
#         errors.append(f"Disk check error: {str(e)[:100]}")
#         return False


# async def _check_memory_health(errors: List[str]) -> bool:
#     """Check memory usage"""
#     try:
#         memory = psutil.virtual_memory()
#         usage_percent = memory.percent

#         # Check critical threshold
#         critical_threshold = health_config.memory_critical or monitoring_config.HEALTH_MEMORY_CRITICAL_PERCENT

#         if usage_percent >= critical_threshold:
#             errors.append(
#                 f"Memory usage critical: {usage_percent:.1f}% "
#                 f"({memory.available // (1024**2)}MB available)"
#             )
#             return False

#         return True

#     except Exception as e:
#         errors.append(f"Memory check error: {str(e)[:100]}")
#         return False


async def _check_queue_health(errors: List[str]) -> bool:
    """Check if ARQ queue is processing jobs"""
    try:
        redis_client = await get_redis_client()

        # Check last job completion time
        last_job_key = monitoring_config.get_redis_key("queue", "last_job_completed")
        last_job_time = await redis_client.get(last_job_key)

        if last_job_time:
            time_diff = time.time() - float(last_job_time)
            stuck_threshold = monitoring_config.HEALTH_QUEUE_STUCK_MINUTES * 60

            if time_diff > stuck_threshold:
                errors.append(
                    f"Queue appears stuck: no jobs completed in "
                    f"{time_diff // 60:.0f} minutes"
                )
                return False

        # УНИВЕРСАЛЬНАЯ ПРОВЕРКА РАЗМЕРА ОЧЕРЕДИ
        queue_size_key = "arq:queue"
        queue_size = 0

        try:
            # Определяем тип ключа
            key_type = await redis_client.type(queue_size_key)

            if key_type == "zset":
                queue_size = await redis_client.zcard(queue_size_key)
            elif key_type == "list":
                queue_size = await redis_client.llen(queue_size_key)  # type: ignore
            elif key_type == "none":
                # Ключ не существует - очередь пуста
                queue_size = 0
            else:
                logger.debug(f"Unknown queue type: {key_type}")
                # Не считаем это ошибкой, просто пропускаем проверку размера

        except Exception as e:
            logger.debug(f"Could not check queue size: {e}")
            # Не критично, продолжаем работу

        if queue_size > 1000:
            errors.append(f"Queue backlog high: {queue_size} jobs pending")

        return True

    except Exception as e:
        errors.append(f"Queue check error: {str(e)[:100]}")
        return False


async def _store_health_status(components: Dict[str, bool], errors: List[str]):
    """Store health status in Redis for monitoring"""
    try:
        redis_client = await get_redis_client()

        # Store current health status
        status_key = monitoring_config.get_redis_key("health", "current")
        status_data = {
            "timestamp": time.time(),
            "healthy": all(components.values()),
            "components": components,
            "errors": errors,
        }

        import json

        await redis_client.setex(
            status_key, 3600, json.dumps(status_data)  # Keep for 1 hour
        )

        # Store in history
        history_key = monitoring_config.get_redis_key("health", "history")
        await redis_client.lpush(history_key, json.dumps(status_data))  # type: ignore
        await redis_client.ltrim(
            history_key, 0, 100
        )  # Keep last 100 checks # type: ignore

    except Exception as e:
        logger.error(f"Failed to store health status: {e}")


@periodic_task
async def send_daily_report(ctx: dict):
    """
    Send daily statistics report.
    Runs once per day at configured time.
    """
    if not monitoring_config.DAILY_REPORT_ENABLED:
        return

    logger.info("Generating daily report...")

    try:
        stats = await _collect_daily_statistics()

        if stats:
            await telegram_reporter.send_daily_report(stats)
            logger.info("Daily report sent successfully")
        else:
            logger.warning("No statistics collected for daily report")

    except Exception as e:
        logger.error(f"Failed to send daily report: {e}")

        # Send error notification
        await telegram_reporter.send_alert(
            title="Daily Report Failed",
            message=f"Failed to generate daily statistics report",
            level=AlertLevel.WARNING,
            error=e,
        )


async def _collect_daily_statistics() -> Dict[str, Any]:
    """Collect statistics for daily report"""
    stats = {}

    try:
        async with async_session_maker() as session:
            now = utc_now()
            yesterday = now - timedelta(days=1)

            # User statistics
            if metrics_config.report_new_users:
                stats["users"] = await _get_user_statistics(session, yesterday, now)

            # Project statistics
            if metrics_config.report_new_projects:
                stats["projects"] = await _get_project_statistics(
                    session, yesterday, now
                )

        # Error statistics from Redis
        if metrics_config.report_errors_summary:
            stats["errors"] = await _get_error_statistics()

        # System statistics
        # stats["system"] = _get_system_statistics()

    except Exception as e:
        logger.error(f"Failed to collect statistics: {e}")

    return stats


async def _get_user_statistics(
    session: AsyncSession, start_date: datetime, end_date: datetime
) -> Dict[str, Any]:
    """Get user-related statistics"""
    try:
        # New users
        new_users_query = (
            select(func.count())
            .select_from(User)
            .where(and_(User.created_at >= start_date, User.created_at < end_date))
        )
        new_users = (await session.scalar(new_users_query)) or 0

        # Active users - using activity feed if available
        active_users = 0
        try:
            # Import here to avoid circular dependency
            from app.activity_feed.models import Activity

            # Count unique users who had any activity in the period
            active_users_query = select(
                func.count(func.distinct(Activity.user_id))
            ).where(and_(Activity.ended_at >= start_date, Activity.ended_at < end_date))
            active_users = (await session.scalar(active_users_query)) or 0

        except ImportError:
            logger.debug(
                "Activity feed module not available, skipping active users count"
            )
        except Exception as e:
            logger.warning(f"Failed to get active users from activity feed: {e}")

        # Total users
        total_users_query = select(func.count()).select_from(User)
        total_users = (await session.scalar(total_users_query)) or 0

        return {"new": new_users, "active": active_users, "total": total_users}

    except Exception as e:
        logger.error(f"Failed to get user statistics: {e}")
        return {}


async def _get_project_statistics(
    session: AsyncSession, start_date: datetime, end_date: datetime
) -> Dict[str, Any]:
    """Get project-related statistics"""
    try:
        # New projects
        new_projects_query = (
            select(func.count())
            .select_from(Project)
            .where(
                and_(Project.created_at >= start_date, Project.created_at < end_date)
            )
        )
        new_projects = (await session.scalar(new_projects_query)) or 0

        # Updated projects
        updated_projects_query = (
            select(func.count())
            .select_from(Project)
            .where(
                and_(
                    Project.updated_at >= start_date,
                    Project.updated_at < end_date,
                    Project.created_at < start_date,  # Not new
                )
            )
        )
        updated_projects = (await session.scalar(updated_projects_query)) or 0

        # Total projects
        total_projects_query = select(func.count()).select_from(Project)
        total_projects = (await session.scalar(total_projects_query)) or 0

        return {
            "created": new_projects,
            "updated": updated_projects,
            "total": total_projects,
        }

    except Exception as e:
        logger.error(f"Failed to get project statistics: {e}")
        return {}


async def _get_error_statistics() -> Dict[str, Any]:
    """Get error statistics from Redis"""
    try:
        redis_client = await get_redis_client()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Get total errors
        total_key = monitoring_config.get_redis_key("stats", today, "errors:total")
        total_errors = await redis_client.get(total_key)
        total_errors = int(total_errors) if total_errors else 0

        # Get errors by type using SCAN for safety
        errors_by_type = {}
        type_pattern = monitoring_config.get_redis_key("stats", today, "errors:type:*")

        # Use SCAN to safely iterate through keys
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor, match=type_pattern, count=100  # Process in batches of 100
            )

            for key in keys:
                # Extract error type from key
                # Key format: monitoring:stats:YYYY-MM-DD:errors:type:ErrorType
                key_parts = key.split(":")
                if len(key_parts) >= 6:
                    error_type = key_parts[-1]
                    count = await redis_client.get(key)
                    if count:
                        errors_by_type[error_type] = int(count)

            # Break when cursor returns to 0
            if cursor == 0:
                break

        # Get slow requests count
        slow_requests = 0
        slow_pattern = monitoring_config.get_redis_key(
            "stats", today, "slow_requests:*"
        )
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor, match=slow_pattern, count=100
            )

            for key in keys:
                if not key.endswith(":times"):  # Skip the times list
                    count = await redis_client.get(key)
                    if count:
                        slow_requests += int(count)

            if cursor == 0:
                break

        return {
            "total": total_errors,
            "by_type": errors_by_type,
            "slow_requests": slow_requests,
        }

    except Exception as e:
        logger.error(f"Failed to get error statistics: {e}")
        return {"total": 0}


# def _get_system_statistics() -> Dict[str, Any]:
#     """Get current system statistics"""
#     try:
#         # System uptime
#         boot_time = datetime.fromtimestamp(psutil.boot_time())
#         uptime = datetime.now() - boot_time
#         uptime_str = f"{uptime.days}d {uptime.seconds // 3600}h"

#         # Disk usage
#         disk_usage = psutil.disk_usage('/')

#         # Memory usage
#         memory = psutil.virtual_memory()

#         return {
#             "uptime": uptime_str,
#             "disk_usage": f"{disk_usage.percent:.1f}",
#             "memory_usage": f"{memory.percent:.1f}"
#         }

#     except Exception as e:
#         logger.error(f"Failed to get system statistics: {e}")
#         return {}


async def mark_job_completed(ctx: dict):
    """
    Helper task to mark that a job was completed.
    Called by other tasks to update last completion time.
    """
    try:
        redis_client = await get_redis_client()
        key = monitoring_config.get_redis_key("queue", "last_job_completed")
        await redis_client.setex(key, 3600, str(time.time()))
    except Exception as e:
        logger.error(f"Failed to mark job completed: {e}")
