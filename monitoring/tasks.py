"""
Monitoring background tasks.

NOTE: This module provides monitoring task functions that should be called
from your background task scheduler (ARQ, Celery, APScheduler, etc.).

Integration examples:

1. With ARQ:
    from arq import cron
    from monitoring.tasks import check_system_health, send_daily_report
    
    class WorkerSettings:
        cron_jobs = [
            cron(check_system_health, minute={0, 30}),  # Every 30 min
            cron(send_daily_report, hour={9}, minute={0})  # Daily at 9 AM
        ]

2. With APScheduler:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from monitoring.tasks import check_system_health, send_daily_report
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_system_health, 'interval', minutes=30)
    scheduler.add_job(send_daily_report, 'cron', hour=9, minute=0)

3. With Celery:
    from celery import Celery
    from monitoring.tasks import check_system_health, send_daily_report
    
    app = Celery('tasks')
    
    @app.task
    def health_check_task():
        import asyncio
        asyncio.run(check_system_health())
    
    app.conf.beat_schedule = {
        'health-check': {
            'task': 'tasks.health_check_task',
            'schedule': 1800.0,  # Every 30 minutes
        }
    }

Make sure to configure adapters before using these tasks:
    from monitoring import set_database_adapter, set_redis_adapter
    
    set_database_adapter(MyDatabaseAdapter())
    set_redis_adapter(MyRedisAdapter())
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import asyncio

from monitoring.config import monitoring_config, AlertLevel
from monitoring.telegram import telegram_reporter
from monitoring import get_database_adapter, get_queue_adapter, get_redis_adapter


logger = logging.getLogger(__name__)


async def check_system_health(ctx: Optional[Dict] = None):
    """
    Periodic health check for all system components.
    
    Checks:
    - Database connectivity and response time
    - Redis connectivity
    - Queue/background job processing
    
    Sends alerts if any component is unhealthy.
    
    Args:
        ctx: Optional context dict (for ARQ compatibility). Can be None.
    
    Usage:
        Schedule this to run every 30 minutes:
        
        # ARQ example:
        cron_jobs = [
            cron(check_system_health, minute={0, 30})
        ]
        
        # APScheduler example:
        scheduler.add_job(check_system_health, 'interval', minutes=30)
    """
    if not monitoring_config.is_enabled:
        return
    
    logger.info("Running system health check...")
    
    errors = []
    components = {}
    
    # Check Database
    db_adapter = get_database_adapter()
    try:
        components["Database"] = await asyncio.wait_for(
            db_adapter.health_check(
                timeout=monitoring_config.HEALTH_DB_TIMEOUT_SECONDS
            ),
            timeout=monitoring_config.HEALTH_DB_TIMEOUT_SECONDS + 1
        )
        if not components["Database"]:
            errors.append("Database health check returned False")
    except asyncio.TimeoutError:
        components["Database"] = False
        errors.append(
            f"Database timeout (>{monitoring_config.HEALTH_DB_TIMEOUT_SECONDS}s)"
        )
    except Exception as e:
        components["Database"] = False
        errors.append(f"Database error: {str(e)[:100]}")
    
    # Check Redis
    redis_adapter = get_redis_adapter()
    if redis_adapter:
        try:
            components["Redis"] = await asyncio.wait_for(
                redis_adapter.ping(),
                timeout=monitoring_config.HEALTH_REDIS_TIMEOUT_SECONDS
            )
            if not components["Redis"]:
                errors.append("Redis ping failed")
        except asyncio.TimeoutError:
            components["Redis"] = False
            errors.append(
                f"Redis timeout (>{monitoring_config.HEALTH_REDIS_TIMEOUT_SECONDS}s)"
            )
        except Exception as e:
            components["Redis"] = False
            errors.append(f"Redis error: {str(e)[:100]}")
    else:
        # Redis is optional
        logger.debug("Redis adapter not configured, skipping Redis health check")
    
    # Check Queue
    queue_adapter = get_queue_adapter()
    try:
        components["Queue"] = await queue_adapter.health_check()
        
        if not components["Queue"]:
            errors.append("Queue health check failed")
        else:
            # Check if queue is stuck
            last_job_time = await queue_adapter.get_last_job_time()
            if last_job_time:
                time_diff = time.time() - last_job_time
                stuck_threshold = monitoring_config.HEALTH_QUEUE_STUCK_MINUTES * 60
                
                if time_diff > stuck_threshold:
                    components["Queue"] = False
                    errors.append(
                        f"Queue stuck: no jobs in {time_diff // 60:.0f} minutes"
                    )
    except Exception as e:
        components["Queue"] = False
        errors.append(f"Queue check error: {str(e)[:100]}")
    
    # Send alert if any component is unhealthy
    if any(not healthy for healthy in components.values()):
        logger.warning(f"Health check failed: {errors}")
        await telegram_reporter.send_health_alert(
            components=components,
            errors=errors
        )
        
        # Store health status
        await _store_health_status(components, errors)
    else:
        logger.info("Health check passed: all systems operational")
        await _store_health_status(components, [])


async def send_daily_report(ctx: Optional[Dict] = None):
    """
    Send daily statistics report.
    
    Collects and reports:
    - User statistics (new, active, total)
    - Project/resource statistics
    - Error statistics
    - Performance metrics
    
    Args:
        ctx: Optional context dict (for ARQ compatibility). Can be None.
    
    Usage:
        Schedule this to run once per day:
        
        # ARQ example:
        cron_jobs = [
            cron(send_daily_report, hour={9}, minute={0})  # 9 AM UTC
        ]
        
        # APScheduler example:
        scheduler.add_job(
            send_daily_report, 
            'cron', 
            hour=9, 
            minute=0
        )
    
    Note:
        Requires database adapter to be configured for statistics.
        Without database adapter, only Redis-based stats will be reported.
    """
    if not monitoring_config.DAILY_REPORT_ENABLED:
        logger.info("Daily report disabled in configuration")
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
        
        await telegram_reporter.send_alert(
            title="Daily Report Failed",
            message="Failed to generate daily statistics report",
            level=AlertLevel.WARNING,
            error=e
        )


async def _collect_daily_statistics() -> Dict[str, Any]:
    """
    Collect statistics using configured adapters.
    
    Returns dictionary with available statistics based on configured adapters:
    - users: User statistics (requires database adapter)
    - projects: Project statistics (requires database adapter)
    - errors: Error statistics (requires Redis adapter)
    """
    stats = {}
    
    db_adapter = get_database_adapter()
    redis_adapter = get_redis_adapter()
    
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)
    
    # Collect database statistics if adapter is configured
    try:
        # Check if using NullDatabaseAdapter
        from monitoring.adapters import NullDatabaseAdapter
        if not isinstance(db_adapter, NullDatabaseAdapter):
            # User statistics
            stats["users"] = {
                "new": await db_adapter.get_new_users_count(yesterday, now),
                "active": await db_adapter.get_active_users_count(yesterday, now),
                "total": await db_adapter.get_total_users_count()
            }
            
            # Project statistics
            stats["projects"] = {
                "created": await db_adapter.get_new_projects_count(yesterday, now),
                "updated": await db_adapter.get_updated_projects_count(yesterday, now),
                "total": await db_adapter.get_total_projects_count()
            }
        else:
            logger.info(
                "Database adapter not configured, skipping database statistics"
            )
    except Exception as e:
        logger.error(f"Failed to collect database statistics: {e}")
    
    # Collect error statistics from Redis
    if redis_adapter:
        try:
            stats["errors"] = await _get_error_statistics(redis_adapter)
        except Exception as e:
            logger.error(f"Failed to collect error statistics: {e}")
    else:
        logger.info("Redis adapter not configured, skipping error statistics")
    
    return stats


async def _get_error_statistics(redis_adapter) -> Dict[str, Any]:
    """Get error statistics from Redis"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    # Get total errors
    total_key = monitoring_config.get_redis_key("stats", today, "errors:total")
    total_errors = await redis_adapter.get(total_key)
    total_errors = int(total_errors) if total_errors else 0
    
    # Get errors by type using SCAN
    errors_by_type = {}
    type_pattern = monitoring_config.get_redis_key("stats", today, "errors:type:*")
    
    cursor = 0
    while True:
        cursor, keys = await redis_adapter.scan(
            cursor, 
            match=type_pattern,
            count=100
        )
        
        for key in keys:
            # Extract error type from key
            key_parts = key.split(":")
            if len(key_parts) >= 6:
                error_type = key_parts[-1]
                count = await redis_adapter.get(key)
                if count:
                    errors_by_type[error_type] = int(count)
        
        if cursor == 0:
            break
    
    # Get slow requests count
    slow_requests = 0
    slow_pattern = monitoring_config.get_redis_key("stats", today, "slow_requests:*")
    cursor = 0
    while True:
        cursor, keys = await redis_adapter.scan(
            cursor,
            match=slow_pattern,
            count=100
        )
        
        for key in keys:
            if not key.endswith(":times"):
                count = await redis_adapter.get(key)
                if count:
                    slow_requests += int(count)
        
        if cursor == 0:
            break
    
    return {
        "total": total_errors,
        "by_type": errors_by_type,
        "slow_requests": slow_requests
    }


async def _store_health_status(components: Dict[str, bool], errors: List[str]):
    """Store health status in Redis for monitoring"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        import json
        
        # Store current health status
        status_key = monitoring_config.get_redis_key("health", "current")
        status_data = {
            "timestamp": time.time(),
            "healthy": all(components.values()),
            "components": components,
            "errors": errors
        }
        
        await redis_adapter.setex(
            status_key,
            3600,  # Keep for 1 hour
            json.dumps(status_data)
        )
        
        # Store in history
        history_key = monitoring_config.get_redis_key("health", "history")
        await redis_adapter.lpush(history_key, json.dumps(status_data))
        await redis_adapter.ltrim(history_key, 0, 100)  # Keep last 100
        
    except Exception as e:
        logger.error(f"Failed to store health status: {e}")


# Helper function for marking job completion (for queue health monitoring)
async def mark_job_completed():
    """
    Call this at the end of successful background jobs.
    Used for queue health monitoring.
    
    Usage:
        @your_task_decorator
        async def my_task(ctx):
            try:
                # Your task logic
                ...
            finally:
                # Mark completion for health monitoring
                await mark_job_completed()
    
    Note:
        Requires Redis adapter to be configured.
    """
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return
    
    try:
        key = monitoring_config.get_redis_key("queue", "last_job_completed")
        await redis_adapter.setex(key, 3600, str(time.time()))
    except Exception as e:
        logger.error(f"Failed to mark job completed: {e}")