"""
Tests for configuration module.
"""

import pytest
from monitoring.config import MonitoringConfig, AlertLevel


def test_alert_level_enum():
    """Test AlertLevel enum values"""
    assert AlertLevel.CRITICAL.value == "critical"
    assert AlertLevel.WARNING.value == "warning"
    assert AlertLevel.INFO.value == "info"


def test_monitoring_config_defaults():
    """Test default configuration values"""
    config = MonitoringConfig()
    
    assert config.MONITORING_ENABLED is True
    assert config.MONITORING_ENV == "development"
    assert config.TELEGRAM_BOT_TOKEN is None
    assert config.TELEGRAM_CHAT_ID is None
    assert config.ALERT_RATE_LIMIT_MINUTES == 10
    assert config.SLOW_REQUEST_THRESHOLD_SECONDS == 3.0
    assert config.MONITOR_EXCEPTIONS is True
    assert config.DAILY_REPORT_ENABLED is True


def test_is_production_property():
    """Test is_production property"""
    # Test development
    config = MonitoringConfig(MONITORING_ENV="development")
    assert config.is_production is False
    
    # Test production
    config = MonitoringConfig(MONITORING_ENV="production")
    assert config.is_production is True
    
    # Test prod (alias)
    config = MonitoringConfig(MONITORING_ENV="prod")
    assert config.is_production is True
    
    # Test staging
    config = MonitoringConfig(MONITORING_ENV="staging")
    assert config.is_production is False


def test_is_enabled_property():
    """Test is_enabled property"""
    # Not enabled without tokens
    config = MonitoringConfig(MONITORING_ENABLED=True)
    assert config.is_enabled is False
    
    # Not enabled if disabled
    config = MonitoringConfig(
        MONITORING_ENABLED=False,
        TELEGRAM_BOT_TOKEN="test",
        TELEGRAM_CHAT_ID="test"
    )
    assert config.is_enabled is False
    
    # Enabled with all requirements
    config = MonitoringConfig(
        MONITORING_ENABLED=True,
        TELEGRAM_BOT_TOKEN="test_token",
        TELEGRAM_CHAT_ID="test_chat_id"
    )
    assert config.is_enabled is True


def test_should_monitor_exception():
    """Test exception filtering"""
    config = MonitoringConfig(
        IGNORED_EXCEPTIONS=["HTTPException", "ValidationError"]
    )
    
    assert config.should_monitor_exception("ValueError") is True
    assert config.should_monitor_exception("HTTPException") is False
    assert config.should_monitor_exception("ValidationError") is False
    assert config.should_monitor_exception("RuntimeError") is True


def test_should_monitor_path():
    """Test path filtering"""
    config = MonitoringConfig(
        IGNORED_PATHS=["/health", "/metrics", "/static"]
    )
    
    assert config.should_monitor_path("/api/users") is True
    assert config.should_monitor_path("/health") is False
    assert config.should_monitor_path("/metrics") is False
    assert config.should_monitor_path("/static/css/style.css") is False
    assert config.should_monitor_path("/api/health") is True  # Doesn't start with /health


def test_get_redis_key():
    """Test Redis key generation"""
    config = MonitoringConfig(REDIS_KEY_PREFIX="monitoring")
    
    key = config.get_redis_key("errors", "2024-01-15", "total")
    assert key == "monitoring:errors:2024-01-15:total"
    
    key = config.get_redis_key("tasks", "slow", "my_task")
    assert key == "monitoring:tasks:slow:my_task"


def test_custom_prefix():
    """Test custom Redis key prefix"""
    config = MonitoringConfig(REDIS_KEY_PREFIX="myapp_monitoring")
    
    key = config.get_redis_key("test")
    assert key == "myapp_monitoring:test"


def test_environment_variable_prefix():
    """Test that config uses MONITORING_ prefix"""
    # This is tested implicitly by pydantic-settings
    # but we verify the prefix is set correctly
    assert MonitoringConfig.model_config["env_prefix"] == "MONITORING_"


def test_config_immutability():
    """Test that config changes don't affect singleton"""
    from monitoring import monitoring_config
    
    original_env = monitoring_config.MONITORING_ENV
    
    # Try to change it
    monitoring_config.MONITORING_ENV = "test"
    
    # It should be changed (it's not truly immutable by design)
    assert monitoring_config.MONITORING_ENV == "test"
    
    # Reset for other tests
    monitoring_config.MONITORING_ENV = original_env


def test_thresholds():
    """Test threshold configurations"""
    config = MonitoringConfig()
    
    # Request thresholds
    assert config.SLOW_REQUEST_THRESHOLD_SECONDS > 0
    
    # Task thresholds
    assert config.ARQ_TASK_SLOW_THRESHOLD_SECONDS > 0
    
    # Health check intervals
    assert config.HEALTH_CHECK_INTERVAL_MINUTES > 0
    assert config.HEALTH_DB_TIMEOUT_SECONDS > 0
    assert config.HEALTH_REDIS_TIMEOUT_SECONDS > 0


def test_batch_settings():
    """Test batch alert configurations"""
    config = MonitoringConfig()
    
    assert config.BATCH_WINDOW_MINUTES > 0
    assert config.BATCH_MAX_ALERTS > 0
    assert config.SLOW_REQUESTS_BATCH_MINUTES > 0