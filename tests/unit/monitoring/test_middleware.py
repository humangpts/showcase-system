"""
Tests for monitoring middleware.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from monitoring.middleware import MonitoringMiddleware, ErrorDeduplicator
from monitoring.config import monitoring_config


@pytest.fixture
def app():
    """Create test FastAPI app"""
    app = FastAPI()
    
    @app.get("/test")
    async def test_endpoint():
        return {"message": "ok"}
    
    @app.get("/error")
    async def error_endpoint():
        raise ValueError("Test error")
    
    @app.get("/http-error")
    async def http_error_endpoint():
        raise HTTPException(status_code=404, detail="Not found")
    
    @app.get("/slow")
    async def slow_endpoint():
        import asyncio
        await asyncio.sleep(0.1)
        return {"message": "slow"}
    
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    # Enable monitoring for tests
    original_enabled = monitoring_config.MONITORING_ENABLED
    monitoring_config.MONITORING_ENABLED = True
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat"
    
    app.add_middleware(MonitoringMiddleware)
    
    yield TestClient(app)
    
    # Restore
    monitoring_config.MONITORING_ENABLED = original_enabled


def test_normal_request(client):
    """Test middleware doesn't interfere with normal requests"""
    response = client.get("/test")
    assert response.status_code == 200
    assert response.json() == {"message": "ok"}


def test_ignored_path():
    """Test that ignored paths are skipped"""
    app = FastAPI()
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    app.add_middleware(MonitoringMiddleware)
    client = TestClient(app)
    
    response = client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_error_deduplicator_fingerprint():
    """Test error fingerprint generation"""
    dedup = ErrorDeduplicator()
    
    error1 = ValueError("Test error")
    error2 = ValueError("Test error")
    error3 = RuntimeError("Different error")
    
    # Same errors should have same fingerprint
    fp1 = dedup.generate_fingerprint("/api/test", "GET", error1)
    fp2 = dedup.generate_fingerprint("/api/test", "GET", error2)
    assert fp1 == fp2
    
    # Different errors should have different fingerprints
    fp3 = dedup.generate_fingerprint("/api/test", "GET", error3)
    assert fp1 != fp3
    
    # Different paths should have different fingerprints
    fp4 = dedup.generate_fingerprint("/api/other", "GET", error1)
    assert fp1 != fp4


@pytest.mark.asyncio
async def test_error_deduplicator_rate_limiting():
    """Test error rate limiting without Redis"""
    dedup = ErrorDeduplicator()
    
    fingerprint = "test_fingerprint"
    
    # First call should allow alert
    assert await dedup.should_send_alert(fingerprint) is True
    
    # Second call should be rate limited
    assert await dedup.should_send_alert(fingerprint) is False


@pytest.mark.asyncio
async def test_error_recording():
    """Test error statistics recording"""
    dedup = ErrorDeduplicator()
    
    # Mock Redis adapter
    mock_redis = AsyncMock()
    
    with patch('monitoring.middleware.get_redis_adapter', return_value=mock_redis):
        await dedup.record_error("/api/test", 500, "ValueError")
        
        # Should have incremented several counters
        assert mock_redis.incr.call_count >= 4  # total, type, endpoint, status


def test_http_exception_handling(client):
    """Test that HTTPException is handled properly"""
    response = client.get("/http-error")
    assert response.status_code == 404


@patch('monitoring.middleware.telegram_reporter.send_alert')
@pytest.mark.asyncio
async def test_exception_alert_sent(mock_send_alert):
    """Test that alerts are sent for exceptions"""
    mock_send_alert.return_value = True
    
    app = FastAPI()
    
    @app.get("/error")
    async def error_endpoint():
        raise ValueError("Test error")
    
    app.add_middleware(MonitoringMiddleware)
    client = TestClient(app)
    
    response = client.get("/error")
    assert response.status_code == 500


def test_monitoring_disabled():
    """Test middleware when monitoring is disabled"""
    monitoring_config.MONITORING_ENABLED = False
    
    app = FastAPI()
    
    @app.get("/test")
    async def test_endpoint():
        return {"message": "ok"}
    
    @app.get("/error")
    async def error_endpoint():
        raise ValueError("Should not be caught")
    
    app.add_middleware(MonitoringMiddleware)
    client = TestClient(app)
    
    # Normal request should work
    response = client.get("/test")
    assert response.status_code == 200
    
    # Error should propagate normally
    with pytest.raises(ValueError):
        client.get("/error")


def test_error_response_format(client):
    """Test error response includes error_id"""
    response = client.get("/error")
    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    assert "error_id" in data


@pytest.mark.asyncio
async def test_slow_request_detection():
    """Test slow request detection"""
    mock_redis = AsyncMock()
    
    with patch('monitoring.middleware.get_redis_adapter', return_value=mock_redis):
        with patch.object(monitoring_config, 'SLOW_REQUEST_THRESHOLD_SECONDS', 0.05):
            with patch.object(monitoring_config, 'MONITOR_SLOW_REQUESTS', True):
                app = FastAPI()
                
                @app.get("/slow")
                async def slow():
                    import asyncio
                    await asyncio.sleep(0.1)
                    return {"ok": True}
                
                app.add_middleware(MonitoringMiddleware)
                client = TestClient(app)
                
                response = client.get("/slow")
                assert response.status_code == 200


def test_ignored_exception_types():
    """Test that ignored exceptions are not monitored"""
    monitoring_config.IGNORED_EXCEPTIONS = ["HTTPException"]
    
    app = FastAPI()
    
    @app.get("/error")
    async def error_endpoint():
        raise HTTPException(status_code=400, detail="Bad request")
    
    app.add_middleware(MonitoringMiddleware)
    client = TestClient(app)
    
    response = client.get("/error")
    assert response.status_code == 400