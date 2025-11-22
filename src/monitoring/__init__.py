"""
Monitoring module for application health and metrics.
Provides exception tracking, health checks, and daily reports via Telegram.
"""

import logging
from fastapi import FastAPI

from app.monitoring.config import monitoring_config, AlertLevel
from app.monitoring.middleware import MonitoringMiddleware
from app.monitoring.telegram import telegram_reporter
from app.monitoring.arq_monitoring import monitored_task, monitored_periodic_task
from app.monitoring.decorators import deduplicated


logger = logging.getLogger(__name__)


def setup_monitoring(app: FastAPI) -> None:
    """
    Setup monitoring for FastAPI application.
    
    Args:
        app: FastAPI application instance
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
    "setup_monitoring",
    "monitoring_config",
    "telegram_reporter",
    "monitored_task",
    "monitored_periodic_task",
]
