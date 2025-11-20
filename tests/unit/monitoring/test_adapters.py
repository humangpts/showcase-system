"""
Tests for adapter interfaces and implementations.
"""

import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime

from monitoring.adapters import (
    DatabaseAdapter,
    QueueAdapter,
    RedisAdapter,
    DefaultRedisAdapter,
    NullDatabaseAdapter,
    NullQueueAdapter,
)


# Test Abstract Interfaces

def test_database_adapter_is_abstract():
    """Test DatabaseAdapter cannot be instantiated"""
    with pytest.raises(TypeError):
        DatabaseAdapter()


def test_queue_adapter_is_abstract():
    """Test QueueAdapter cannot be instantiated"""
    with pytest.raises(TypeError):
        QueueAdapter()


def test_redis_adapter_is_abstract():
    """Test RedisAdapter cannot be instantiated"""
    with pytest.raises(TypeError):
        RedisAdapter()


# Test NullDatabaseAdapter

@pytest.mark.asyncio
async def test_null_database_adapter():
    """Test NullDatabaseAdapter returns expected defaults"""
    adapter = NullDatabaseAdapter()
    
    now = datetime.utcnow()
    
    assert await adapter.get_new_users_count(now, now) == 0
    assert await adapter.get_active_users_count(now, now) == 0
    assert await adapter.get_total_users_count() == 0
    assert await adapter.get_new_projects_count(now, now) == 0
    assert await adapter.get_updated_projects_count(now, now) == 0
    assert await adapter.get_total_projects_count() == 0
    assert await adapter.health_check() is True
    assert await adapter.health_check(timeout=10.0) is True


# Test NullQueueAdapter

@pytest.mark.asyncio
async def test_null_queue_adapter():
    """Test NullQueueAdapter returns expected defaults"""
    adapter = NullQueueAdapter()
    
    assert await adapter.health_check() is True
    assert await adapter.get_queue_size() == 0
    assert await adapter.get_last_job_time() is None


# Test DefaultRedisAdapter

@pytest.mark.asyncio
async def test_default_redis_adapter_get():
    """Test DefaultRedisAdapter get method"""
    mock_client = AsyncMock()
    mock_client.get.return_value = b"test_value"
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.get("test_key")
    
    assert result == "test_value"
    mock_client.get.assert_called_once_with("test_key")


@pytest.mark.asyncio
async def test_default_redis_adapter_get_string():
    """Test DefaultRedisAdapter get with string value"""
    mock_client = AsyncMock()
    mock_client.get.return_value = "test_value"
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.get("test_key")
    
    assert result == "test_value"


@pytest.mark.asyncio
async def test_default_redis_adapter_set():
    """Test DefaultRedisAdapter set method"""
    mock_client = AsyncMock()
    mock_client.set.return_value = True
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.set("key", "value", ex=60)
    
    assert result is True
    mock_client.set.assert_called_once_with("key", "value", ex=60, nx=False, xx=False)


@pytest.mark.asyncio
async def test_default_redis_adapter_setex():
    """Test DefaultRedisAdapter setex method"""
    mock_client = AsyncMock()
    mock_client.setex.return_value = True
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.setex("key", 300, "value")
    
    assert result is True
    mock_client.setex.assert_called_once_with("key", 300, "value")


@pytest.mark.asyncio
async def test_default_redis_adapter_delete():
    """Test DefaultRedisAdapter delete method"""
    mock_client = AsyncMock()
    mock_client.delete.return_value = 1
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.delete("key")
    
    assert result == 1
    mock_client.delete.assert_called_once_with("key")


@pytest.mark.asyncio
async def test_default_redis_adapter_incr():
    """Test DefaultRedisAdapter incr method"""
    mock_client = AsyncMock()
    mock_client.incr.return_value = 5
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.incr("counter")
    
    assert result == 5
    mock_client.incr.assert_called_once_with("counter")


@pytest.mark.asyncio
async def test_default_redis_adapter_expire():
    """Test DefaultRedisAdapter expire method"""
    mock_client = AsyncMock()
    mock_client.expire.return_value = True
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.expire("key", 600)
    
    assert result is True
    mock_client.expire.assert_called_once_with("key", 600)


@pytest.mark.asyncio
async def test_default_redis_adapter_lpush():
    """Test DefaultRedisAdapter lpush method"""
    mock_client = AsyncMock()
    mock_client.lpush.return_value = 3
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.lpush("list", "val1", "val2")
    
    assert result == 3
    mock_client.lpush.assert_called_once_with("list", "val1", "val2")


@pytest.mark.asyncio
async def test_default_redis_adapter_lrange():
    """Test DefaultRedisAdapter lrange method"""
    mock_client = AsyncMock()
    mock_client.lrange.return_value = [b"val1", b"val2", "val3"]
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.lrange("list", 0, -1)
    
    assert result == ["val1", "val2", "val3"]
    mock_client.lrange.assert_called_once_with("list", 0, -1)


@pytest.mark.asyncio
async def test_default_redis_adapter_ltrim():
    """Test DefaultRedisAdapter ltrim method"""
    mock_client = AsyncMock()
    mock_client.ltrim.return_value = True
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.ltrim("list", 0, 99)
    
    assert result is True
    mock_client.ltrim.assert_called_once_with("list", 0, 99)


@pytest.mark.asyncio
async def test_default_redis_adapter_scan():
    """Test DefaultRedisAdapter scan method"""
    mock_client = AsyncMock()
    mock_client.scan.return_value = (10, [b"key1", b"key2", "key3"])
    
    adapter = DefaultRedisAdapter(mock_client)
    
    cursor, keys = await adapter.scan(0, match="test:*", count=100)
    
    assert cursor == 10
    assert keys == ["key1", "key2", "key3"]
    mock_client.scan.assert_called_once_with(0, match="test:*", count=100)


@pytest.mark.asyncio
async def test_default_redis_adapter_ping():
    """Test DefaultRedisAdapter ping method"""
    mock_client = AsyncMock()
    mock_client.ping.return_value = True
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.ping()
    
    assert result is True
    mock_client.ping.assert_called_once()


@pytest.mark.asyncio
async def test_default_redis_adapter_type():
    """Test DefaultRedisAdapter type method"""
    mock_client = AsyncMock()
    mock_client.type.return_value = b"string"
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.type("key")
    
    assert result == "string"
    mock_client.type.assert_called_once_with("key")


@pytest.mark.asyncio
async def test_default_redis_adapter_zcard():
    """Test DefaultRedisAdapter zcard method"""
    mock_client = AsyncMock()
    mock_client.zcard.return_value = 10
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.zcard("sorted_set")
    
    assert result == 10
    mock_client.zcard.assert_called_once_with("sorted_set")


@pytest.mark.asyncio
async def test_default_redis_adapter_llen():
    """Test DefaultRedisAdapter llen method"""
    mock_client = AsyncMock()
    mock_client.llen.return_value = 5
    
    adapter = DefaultRedisAdapter(mock_client)
    
    result = await adapter.llen("list")
    
    assert result == 5
    mock_client.llen.assert_called_once_with("list")


# Test Custom Adapter Implementation

class TestDatabaseAdapter(DatabaseAdapter):
    """Test implementation of DatabaseAdapter"""
    
    async def get_new_users_count(self, start_date, end_date):
        return 10
    
    async def get_active_users_count(self, start_date, end_date):
        return 50
    
    async def get_total_users_count(self):
        return 100
    
    async def get_new_projects_count(self, start_date, end_date):
        return 5
    
    async def get_updated_projects_count(self, start_date, end_date):
        return 20
    
    async def get_total_projects_count(self):
        return 50
    
    async def health_check(self, timeout=5.0):
        return True


@pytest.mark.asyncio
async def test_custom_database_adapter():
    """Test custom DatabaseAdapter implementation"""
    adapter = TestDatabaseAdapter()
    
    now = datetime.utcnow()
    
    assert await adapter.get_new_users_count(now, now) == 10
    assert await adapter.get_active_users_count(now, now) == 50
    assert await adapter.get_total_users_count() == 100
    assert await adapter.get_new_projects_count(now, now) == 5
    assert await adapter.get_updated_projects_count(now, now) == 20
    assert await adapter.get_total_projects_count() == 50
    assert await adapter.health_check() is True


class TestQueueAdapter(QueueAdapter):
    """Test implementation of QueueAdapter"""
    
    async def health_check(self):
        return True
    
    async def get_queue_size(self):
        return 10
    
    async def get_last_job_time(self):
        return 1234567890.0


@pytest.mark.asyncio
async def test_custom_queue_adapter():
    """Test custom QueueAdapter implementation"""
    adapter = TestQueueAdapter()
    
    assert await adapter.health_check() is True
    assert await adapter.get_queue_size() == 10
    assert await adapter.get_last_job_time() == 1234567890.0