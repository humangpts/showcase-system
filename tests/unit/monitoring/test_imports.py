"""
Test that all public imports work correctly.
This ensures the package is properly structured.
"""

import pytest


def test_main_imports():
    """Test importing main components"""
    from monitoring import (
        setup_monitoring,
        monitoring_config,
        AlertLevel,
        telegram_reporter,
    )
    
    assert setup_monitoring is not None
    assert monitoring_config is not None
    assert AlertLevel is not None
    assert telegram_reporter is not None


def test_adapter_imports():
    """Test importing adapters"""
    from monitoring import (
        DatabaseAdapter,
        QueueAdapter,
        RedisAdapter,
        DefaultRedisAdapter,
        NullDatabaseAdapter,
        NullQueueAdapter,
    )
    
    assert DatabaseAdapter is not None
    assert QueueAdapter is not None
    assert RedisAdapter is not None
    assert DefaultRedisAdapter is not None
    assert NullDatabaseAdapter is not None
    assert NullQueueAdapter is not None


def test_adapter_setters():
    """Test adapter setter functions"""
    from monitoring import (
        set_database_adapter,
        set_queue_adapter,
        set_redis_adapter,
        get_database_adapter,
        get_queue_adapter,
        get_redis_adapter,
    )
    
    assert set_database_adapter is not None
    assert set_queue_adapter is not None
    assert set_redis_adapter is not None
    assert get_database_adapter is not None
    assert get_queue_adapter is not None
    assert get_redis_adapter is not None


def test_decorator_imports():
    """Test importing decorators"""
    from monitoring import deduplicated
    
    assert deduplicated is not None


def test_optional_arq_imports():
    """Test ARQ monitoring imports (may not be available)"""
    try:
        from monitoring import (
            monitored_task,
            monitored_periodic_task,
            ARQ_AVAILABLE,
        )
        
        assert ARQ_AVAILABLE is not None
        # If ARQ is not available, these should be None
        if not ARQ_AVAILABLE:
            assert monitored_task is None
            assert monitored_periodic_task is None
    except ImportError:
        # It's OK if ARQ is not installed
        pass


def test_config_properties():
    """Test configuration object has expected properties"""
    from monitoring import monitoring_config
    
    # Check key properties exist
    assert hasattr(monitoring_config, 'MONITORING_ENABLED')
    assert hasattr(monitoring_config, 'MONITORING_ENV')
    assert hasattr(monitoring_config, 'TELEGRAM_BOT_TOKEN')
    assert hasattr(monitoring_config, 'TELEGRAM_CHAT_ID')
    assert hasattr(monitoring_config, 'is_enabled')
    assert hasattr(monitoring_config, 'is_production')


def test_alert_levels():
    """Test AlertLevel enum"""
    from monitoring import AlertLevel
    
    assert AlertLevel.CRITICAL == "critical"
    assert AlertLevel.WARNING == "warning"
    assert AlertLevel.INFO == "info"


def test_null_adapters_work():
    """Test that null adapters can be instantiated"""
    from monitoring import NullDatabaseAdapter, NullQueueAdapter
    
    db_adapter = NullDatabaseAdapter()
    queue_adapter = NullQueueAdapter()
    
    assert db_adapter is not None
    assert queue_adapter is not None


@pytest.mark.asyncio
async def test_null_adapters_methods():
    """Test null adapter methods return expected defaults"""
    from monitoring import NullDatabaseAdapter, NullQueueAdapter
    from datetime import datetime
    
    db_adapter = NullDatabaseAdapter()
    queue_adapter = NullQueueAdapter()
    
    # Database adapter
    assert await db_adapter.get_total_users_count() == 0
    assert await db_adapter.get_total_projects_count() == 0
    assert await db_adapter.health_check() is True
    
    # Queue adapter
    assert await queue_adapter.get_queue_size() == 0
    assert await queue_adapter.health_check() is True
    assert await queue_adapter.get_last_job_time() is None


def test_version():
    """Test package has version"""
    from monitoring import __version__
    
    assert __version__ is not None
    assert isinstance(__version__, str)
    assert __version__ == "1.0.0"