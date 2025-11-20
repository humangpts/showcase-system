"""
Monitoring module configuration.
Centralized settings for all monitoring components.
"""

from typing import Optional, List
from pydantic import Field
from pydantic_settings import BaseSettings
from enum import Enum


class AlertLevel(str, Enum):
    """Alert severity levels"""
    CRITICAL = "critical"  # Immediate notification
    WARNING = "warning"    # Batched notifications
    INFO = "info"         # Daily digest


class MonitoringConfig(BaseSettings):
    """Main monitoring configuration"""
    
    # Feature flags
    MONITORING_ENABLED: bool = Field(
        default=True,
        description="Enable monitoring system"
    )
    
    MONITORING_ENV: str = Field(
        default="development",
        description="Environment name for alerts (development/staging/production)"
    )
    
    # Telegram settings
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(
        default=None,
        description="Telegram bot token"
    )
    
    TELEGRAM_CHAT_ID: Optional[str] = Field(
        default=None,
        description="Telegram chat/channel ID for alerts"
    )
    
    TELEGRAM_THREAD_ID: Optional[int] = Field(
        default=None,
        description="Telegram thread ID for grouping messages"
    )
    
    # Alert settings
    ALERT_RATE_LIMIT_MINUTES: int = Field(
        default=10,
        description="Minimum minutes between same error alerts"
    )
    
    ALERT_MAX_TRACEBACK_LINES: int = Field(
        default=15,
        description="Maximum traceback lines in error alerts"
    )
    
    ALERT_MAX_MESSAGE_LENGTH: int = Field(
        default=4000,
        description="Maximum Telegram message length"
    )
    
    ALERT_FIRE_AND_FORGET: bool = Field(
        default=True,
        description=(
            "Send alerts asynchronously without blocking request response. "
            "If False, alerts are sent synchronously (may slow down responses)."
        )
    )
    
    # Health check settings
    HEALTH_CHECK_INTERVAL_MINUTES: int = Field(
        default=30,
        description="Interval for health checks"
    )
    
    HEALTH_DB_TIMEOUT_SECONDS: int = Field(
        default=5,
        description="Database health check timeout"
    )
    
    HEALTH_REDIS_TIMEOUT_SECONDS: int = Field(
        default=3,
        description="Redis health check timeout"
    )
    
    HEALTH_QUEUE_STUCK_MINUTES: int = Field(
        default=10,
        description="Minutes before queue considered stuck"
    )
    
    # Daily report settings
    DAILY_REPORT_ENABLED: bool = Field(
        default=True,
        description="Enable daily reports"
    )
    
    DAILY_REPORT_HOUR: int = Field(
        default=9,
        description="Hour to send daily report (0-23, UTC)"
    )
    
    DAILY_REPORT_MINUTE: int = Field(
        default=0,
        description="Minute to send daily report (0-59)"
    )
    
    # Exception monitoring
    MONITOR_EXCEPTIONS: bool = Field(
        default=True,
        description="Monitor and report exceptions"
    )
    
    IGNORED_EXCEPTIONS: List[str] = Field(
        default=[
            "HTTPException",
            "RequestValidationError",
        ],
        description="Exception types to ignore"
    )
    
    IGNORED_PATHS: List[str] = Field(
        default=[
            "/health",
            "/metrics",
            "/static",
            "/docs",
            "/redoc",
            "/openapi.json",
        ],
        description="URL paths to ignore for monitoring"
    )
    
    # Performance monitoring
    SLOW_REQUEST_THRESHOLD_SECONDS: float = Field(
        default=3.0,
        description="Threshold for slow request alerts"
    )
    
    MONITOR_SLOW_REQUESTS: bool = Field(
        default=True,
        description="Monitor slow requests"
    )
    
    SLOW_REQUESTS_BATCH_MINUTES: int = Field(
        default=15,
        description="Batch slow requests alerts for this many minutes"
    )
    
    # ARQ/Queue monitoring
    MONITOR_ARQ_TASKS: bool = Field(
        default=True,
        description="Monitor ARQ background tasks"
    )
    
    ARQ_TASK_FAILURE_ALERT: bool = Field(
        default=True,
        description="Alert on task failures"
    )
    
    ARQ_TASK_SLOW_THRESHOLD_SECONDS: float = Field(
        default=60.0,
        description="Threshold for slow background task alerts"
    )
    
    ARQ_IGNORED_TASKS: List[str] = Field(
        default=[
            "mark_job_completed",
        ],
        description="Task names to ignore for monitoring"
    )
    
    # Batching settings
    BATCH_WINDOW_MINUTES: int = Field(
        default=15,
        description="Window for batching non-critical alerts"
    )
    
    BATCH_MAX_ALERTS: int = Field(
        default=10,
        description="Maximum alerts in one batch"
    )
    
    # Redis keys configuration
    REDIS_KEY_PREFIX: str = Field(
        default="monitoring",
        description="Prefix for monitoring Redis keys"
    )
    
    REDIS_KEY_TTL_HOURS: int = Field(
        default=24,
        description="TTL for monitoring data in Redis"
    )

    # Security settings
    ALERT_SANITIZE_HEADERS: bool = Field(
        default=True,
        description="Remove sensitive headers from alerts (Authorization, Cookie, etc.)"
    )
    
    ALERT_SANITIZE_TRACEBACK: bool = Field(
        default=True,
        description="Sanitize sensitive data in tracebacks (passwords, tokens, etc.)"
    )
    
    ALERT_SANITIZE_QUERY_PARAMS: bool = Field(
        default=True,
        description="Sanitize sensitive query parameters (token, api_key, etc.)"
    )
    
    ALERT_INCLUDE_REQUEST_BODY: bool = Field(
        default=False,
        description=(
            "Include request body in error alerts. "
            "WARNING: This may expose sensitive data. Only enable in development."
        )
    )
    
    ALERT_MAX_STRING_LENGTH: int = Field(
        default=500,
        description="Maximum length for string values in alerts"
    )
    
    # Additional sensitive header patterns (in addition to defaults)
    ALERT_ADDITIONAL_SENSITIVE_HEADERS: List[str] = Field(
        default=[],
        description="Additional header names to consider sensitive"
    )
    
    # Additional sensitive URL parameter patterns
    ALERT_ADDITIONAL_SENSITIVE_PARAMS: List[str] = Field(
        default=[],
        description="Additional URL parameter names to consider sensitive"
    )
    
    # Telegram connection settings
    TELEGRAM_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description="Timeout for Telegram API requests"
    )
    
    TELEGRAM_RETRY_ATTEMPTS: int = Field(
        default=3,
        description="Number of retry attempts for failed Telegram requests"
    )
    
    model_config = {
        "env_prefix": "MONITORING_",  # All env vars start with MONITORING_
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"
    }
    
    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.MONITORING_ENV.lower() in ["production", "prod"]
    
    @property
    def is_enabled(self) -> bool:
        """Check if monitoring is fully configured and enabled"""
        return (
            self.MONITORING_ENABLED
            and self.TELEGRAM_BOT_TOKEN is not None
            and self.TELEGRAM_CHAT_ID is not None
        )
    
    def get_all_sensitive_headers(self) -> set:
        """
        Get complete set of sensitive headers including defaults and user-defined.
        
        Returns:
            Set of lowercase header names to filter
        """
        from monitoring.security_utils import SENSITIVE_HEADERS
        
        all_headers = set(SENSITIVE_HEADERS)
        all_headers.update(
            h.lower() for h in self.ALERT_ADDITIONAL_SENSITIVE_HEADERS
        )
        
        return all_headers
    
    def get_all_sensitive_params(self) -> set:
        """
        Get complete set of sensitive URL parameters.
        
        Returns:
            Set of lowercase parameter names to filter
        """
        default_params = {'token', 'api_key', 'apikey', 'key', 'secret', 'password', 'auth'}
        
        all_params = set(default_params)
        all_params.update(
            p.lower() for p in self.ALERT_ADDITIONAL_SENSITIVE_PARAMS
        )
        
        return all_params
    
    def should_monitor_exception(self, exception_type: str) -> bool:
        """Check if exception type should be monitored"""
        return exception_type not in self.IGNORED_EXCEPTIONS
    
    def should_monitor_path(self, path: str) -> bool:
        """Check if path should be monitored"""
        for ignored in self.IGNORED_PATHS:
            if path.startswith(ignored):
                return False
        return True
    
    def get_redis_key(self, *parts: str) -> str:
        """Generate Redis key with prefix"""
        return f"{self.REDIS_KEY_PREFIX}:{':'.join(parts)}"


# Singleton instance
monitoring_config = MonitoringConfig()


# Validation
import logging
logger = logging.getLogger(__name__)

if monitoring_config.is_enabled:
    logger.info(
        f"Monitoring enabled for environment: {monitoring_config.MONITORING_ENV}"
    )
    
    if not monitoring_config.is_production:
        logger.warning(
            "Monitoring is enabled in non-production environment. "
            "Consider adjusting alert thresholds."
        )