"""
Monitoring module for FastAPI applications.
Provides exception tracking, health checks, and daily reports via Telegram.

Basic usage:
    from monitoring import setup_monitoring, monitoring_config
    
    # Configure
    monitoring_config.TELEGRAM_BOT_TOKEN = "your_token"
    monitoring_config.TELEGRAM_CHAT_ID = "your_chat_id"
    
    # Setup
    app = FastAPI()
    setup_monitoring(app)

Advanced usage with adapters:
    from monitoring import setup_monitoring, monitoring_config
    from monitoring.adapters import DatabaseAdapter, QueueAdapter
    
    # Implement your adapters
    class MyDatabaseAdapter(DatabaseAdapter):
        ...
    
    # Configure
    monitoring_config.database_adapter = MyDatabaseAdapter()
    monitoring_config.queue_adapter = MyQueueAdapter()
    
    # Setup
    setup_monitoring(app)
"""

import logging
from typing import Optional
from fastapi import FastAPI

from monitoring.config import monitoring_config, AlertLevel
from monitoring.middleware import MonitoringMiddleware
from monitoring.telegram import telegram_reporter
from monitoring.decorators import deduplicated
from monitoring.adapters import (
    DatabaseAdapter,
    QueueAdapter,
    RedisAdapter,
    DefaultRedisAdapter,
    NullDatabaseAdapter,
    NullQueueAdapter,
)

# Optional imports (for ARQ monitoring)
try:
    from monitoring.arq_monitoring import monitored_task, monitored_periodic_task
    ARQ_AVAILABLE = True
except ImportError:
    ARQ_AVAILABLE = False
    monitored_task = None
    monitored_periodic_task = None


logger = logging.getLogger(__name__)

# Global adapters (can be set by user)
_database_adapter: Optional[DatabaseAdapter] = None
_queue_adapter: Optional[QueueAdapter] = None
_redis_adapter: Optional[RedisAdapter] = None


def set_database_adapter(adapter: DatabaseAdapter) -> None:
    """
    Set database adapter for monitoring statistics.
    
    Args:
        adapter: Implementation of DatabaseAdapter
        
    Example:
        >>> from monitoring import set_database_adapter
        >>> from monitoring.adapters import DatabaseAdapter
        >>> 
        >>> class MyAdapter(DatabaseAdapter):
        >>>     async def get_new_users_count(self, start, end):
        >>>         # Your implementation
        >>>         pass
        >>> 
        >>> set_database_adapter(MyAdapter())
    """
    global _database_adapter
    _database_adapter = adapter
    logger.info("Database adapter configured")


def set_queue_adapter(adapter: QueueAdapter) -> None:
    """
    Set queue adapter for monitoring background tasks.
    
    Args:
        adapter: Implementation of QueueAdapter
    """
    global _queue_adapter
    _queue_adapter = adapter
    logger.info("Queue adapter configured")


def set_redis_adapter(adapter: RedisAdapter) -> None:
    """
    Set Redis adapter for caching and deduplication.
    
    Args:
        adapter: Implementation of RedisAdapter
    """
    global _redis_adapter
    _redis_adapter = adapter
    logger.info("Redis adapter configured")


def get_database_adapter() -> DatabaseAdapter:
    """Get configured database adapter or null adapter"""
    if _database_adapter:
        return _database_adapter
    return NullDatabaseAdapter()


def get_queue_adapter() -> QueueAdapter:
    """Get configured queue adapter or null adapter"""
    if _queue_adapter:
        return _queue_adapter
    return NullQueueAdapter()


def get_redis_adapter() -> Optional[RedisAdapter]:
    """Get configured Redis adapter"""
    return _redis_adapter


def setup_monitoring(
    app: FastAPI,
    redis_client = None,
    database_adapter: Optional[DatabaseAdapter] = None,
    queue_adapter: Optional[QueueAdapter] = None,
) -> None:
    """
    Setup monitoring for FastAPI application.
    
    Args:
        app: FastAPI application instance
        redis_client: Optional redis.asyncio.Redis client for default adapter
        database_adapter: Optional database adapter for statistics
        queue_adapter: Optional queue adapter for health checks
        
    Example:
        Basic setup:
        >>> from fastapi import FastAPI
        >>> from monitoring import setup_monitoring, monitoring_config
        >>> 
        >>> monitoring_config.TELEGRAM_BOT_TOKEN = "your_token"
        >>> monitoring_config.TELEGRAM_CHAT_ID = "your_chat_id"
        >>> 
        >>> app = FastAPI()
        >>> setup_monitoring(app)
        
        With Redis:
        >>> from redis import asyncio as aioredis
        >>> redis_client = aioredis.from_url("redis://localhost")
        >>> setup_monitoring(app, redis_client=redis_client)
        
        With custom adapters:
        >>> from monitoring.adapters import DatabaseAdapter
        >>> 
        >>> class MyDatabaseAdapter(DatabaseAdapter):
        >>>     # Implement methods
        >>>     pass
        >>> 
        >>> setup_monitoring(
        >>>     app,
        >>>     database_adapter=MyDatabaseAdapter(),
        >>>     queue_adapter=MyQueueAdapter()
        >>> )
    """
    
    if not monitoring_config.MONITORING_ENABLED:
        logger.info("Monitoring is disabled")
        return
    
    # Validate configuration
    if not monitoring_config.TELEGRAM_BOT_TOKEN:
        logger.warning(
            "Monitoring enabled but TELEGRAM_BOT_TOKEN not set. "
            "Monitoring will be limited."
        )
        return
    
    if not monitoring_config.TELEGRAM_CHAT_ID:
        logger.warning(
            "Monitoring enabled but TELEGRAM_CHAT_ID not set. "
            "Monitoring will be limited."
        )
        return
    
    # Setup Redis adapter if client provided
    if redis_client and not _redis_adapter:
        set_redis_adapter(DefaultRedisAdapter(redis_client))
    
    # Setup database adapter if provided
    if database_adapter:
        set_database_adapter(database_adapter)
    
    # Setup queue adapter if provided
    if queue_adapter:
        set_queue_adapter(queue_adapter)
    
    # Add exception monitoring middleware
    if monitoring_config.MONITOR_EXCEPTIONS:
        app.add_middleware(MonitoringMiddleware)
        logger.info("Exception monitoring middleware added")
    
    # Log configuration
    logger.info(
        f"Monitoring setup complete for environment: {monitoring_config.MONITORING_ENV}"
    )
    logger.info(f"- Exceptions: {monitoring_config.MONITOR_EXCEPTIONS}")
    logger.info(f"- Daily reports: {monitoring_config.DAILY_REPORT_ENABLED}")
    logger.info(f"- Health checks: every {monitoring_config.HEALTH_CHECK_INTERVAL_MINUTES} minutes")
    logger.info(f"- Rate limit: {monitoring_config.ALERT_RATE_LIMIT_MINUTES} minutes")
    logger.info(f"- Database adapter: {'configured' if _database_adapter else 'not configured'}")
    logger.info(f"- Queue adapter: {'configured' if _queue_adapter else 'not configured'}")
    logger.info(f"- Redis adapter: {'configured' if _redis_adapter else 'not configured'}")


@deduplicated(key="startup_notification", ttl=60)
async def send_startup_notification() -> None:
    """Send notification that application has started"""
    if not monitoring_config.is_production:
        return 
    
    try:
        await telegram_reporter.send_message(
            text=(
                f"🚀 *Application Started*\n"
                f"Environment: {monitoring_config.MONITORING_ENV}\n"
                f"Monitoring: Active"
            ),
            level=AlertLevel.INFO,
            disable_notification=True
        )
    except Exception as e:
        logger.error(f"Failed to send startup notification: {e}")


# Export main components
__all__ = [
    # Setup
    "setup_monitoring",
    "send_startup_notification",
    
    # Configuration
    "monitoring_config",
    "AlertLevel",
    
    # Adapters
    "set_database_adapter",
    "set_queue_adapter",
    "set_redis_adapter",
    "get_database_adapter",
    "get_queue_adapter",
    "get_redis_adapter",
    "DatabaseAdapter",
    "QueueAdapter",
    "RedisAdapter",
    "DefaultRedisAdapter",
    "NullDatabaseAdapter",
    "NullQueueAdapter",
    
    # Telegram
    "telegram_reporter",
    
    # Decorators
    "deduplicated",
    
    # ARQ monitoring (if available)
    "monitored_task",
    "monitored_periodic_task",
    "ARQ_AVAILABLE",
]

# Version
__version__ = "1.0.0"