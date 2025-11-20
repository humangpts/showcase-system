"""
Integration tests for complete monitoring setup.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from monitoring import (
    setup_monitoring,
    monitoring_config,
    set_database_adapter,
    set_redis_adapter,
    get_database_adapter,
    get_redis_adapter,
)


@pytest.fixture
def clean_app():
    """Create clean FastAPI app"""
    app = FastAPI()
    
    @app.get("/")
    async def root():
        return {"status": "ok"}
    
    @app.get("/error")
    async def error():
        raise ValueError("Test error")
    
    return app


def test_setup_monitoring_basic(clean_app):
    """Test basic monitoring setup"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    # Should not raise
    setup_monitoring(clean_app)
    
    # Middleware should be added
    assert len(clean_app.user_middleware) > 0


def test_setup_monitoring_disabled(clean_app):
    """Test monitoring setup when disabled"""
    monitoring_config.MONITORING_ENABLED = False
    
    setup_monitoring(clean_app)
    
    # No middleware should be added
    assert len(clean_app.user_middleware) == 0


def test_setup_monitoring_missing_config(clean_app):
    """Test monitoring setup with missing configuration"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = None
    monitoring_config.TELEGRAM_CHAT_ID = None
    
    # Should not raise, but should warn
    setup_monitoring(clean_app)


def test_setup_monitoring_with_redis(clean_app):
    """Test monitoring setup with Redis"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    mock_redis_client = AsyncMock()
    
    setup_monitoring(clean_app, redis_client=mock_redis_client)
    
    # Redis adapter should be configured
    redis_adapter = get_redis_adapter()
    assert redis_adapter is not None


def test_setup_monitoring_with_adapters(clean_app, mock_database_adapter, mock_queue_adapter):
    """Test monitoring setup with custom adapters"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    setup_monitoring(
        clean_app,
        database_adapter=mock_database_adapter,
        queue_adapter=mock_queue_adapter
    )
    
    # Adapters should be configured
    db_adapter = get_database_adapter()
    assert db_adapter is mock_database_adapter


@patch('monitoring.telegram_reporter.send_alert')
def test_full_error_flow(mock_send_alert, clean_app):
    """Test complete error tracking flow"""
    mock_send_alert.return_value = True
    
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    setup_monitoring(clean_app)
    
    client = TestClient(clean_app)
    
    # Trigger error
    response = client.get("/error")
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_adapter_integration(mock_database_adapter):
    """Test database adapter integration"""
    set_database_adapter(mock_database_adapter)
    
    db = get_database_adapter()
    
    # Should be able to use adapter methods
    total_users = await db.get_total_users_count()
    assert total_users == 100
    
    health = await db.health_check()
    assert health is True


def test_multiple_setup_calls(clean_app):
    """Test that multiple setup_monitoring calls don't break things"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    setup_monitoring(clean_app)
    middleware_count_1 = len(clean_app.user_middleware)
    
    setup_monitoring(clean_app)
    middleware_count_2 = len(clean_app.user_middleware)
    
    # Should add middleware each time
    assert middleware_count_2 == middleware_count_1 * 2


def test_monitoring_in_production_mode(clean_app):
    """Test monitoring behavior in production"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.MONITORING_ENV = "production"
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    setup_monitoring(clean_app)
    
    assert monitoring_config.is_production is True


def test_monitoring_in_development_mode(clean_app):
    """Test monitoring behavior in development"""
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.MONITORING_ENV = "development"
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    setup_monitoring(clean_app)
    
    assert monitoring_config.is_production is False


@pytest.mark.asyncio
async def test_redis_adapter_integration():
    """Test Redis adapter integration"""
    mock_redis_client = AsyncMock()
    mock_redis_client.ping.return_value = True
    
    from monitoring.adapters import DefaultRedisAdapter
    
    adapter = DefaultRedisAdapter(mock_redis_client)
    set_redis_adapter(adapter)
    
    redis = get_redis_adapter()
    assert redis is not None
    
    # Test basic operations
    await redis.set("test_key", "test_value")
    mock_redis_client.set.assert_called_once()
    
    health = await redis.ping()
    assert health is True