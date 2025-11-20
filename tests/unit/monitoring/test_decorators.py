"""
Tests for decorators.
"""

import pytest
from unittest.mock import AsyncMock, patch

from monitoring.decorators import deduplicated


@pytest.mark.asyncio
async def test_deduplicated_without_redis():
    """Test deduplicated decorator without Redis adapter"""
    call_count = 0
    
    @deduplicated(key="test_func", ttl=60)
    async def test_func():
        nonlocal call_count
        call_count += 1
        return "executed"
    
    # Without Redis, should execute every time
    result1 = await test_func()
    assert result1 == "executed"
    assert call_count == 1
    
    result2 = await test_func()
    assert result2 == "executed"
    assert call_count == 2


@pytest.mark.asyncio
async def test_deduplicated_with_redis():
    """Test deduplicated decorator with Redis adapter"""
    call_count = 0
    
    mock_redis = AsyncMock()
    
    # First call: set returns True (key was new)
    # Second call: set returns False (key exists)
    mock_redis.set.side_effect = [True, False, False]
    
    @deduplicated(key="test_func", ttl=60)
    async def test_func():
        nonlocal call_count
        call_count += 1
        return "executed"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        # First call should execute
        result1 = await test_func()
        assert result1 == "executed"
        assert call_count == 1
        
        # Second call should be deduplicated
        result2 = await test_func()
        assert result2 is None
        assert call_count == 1  # Should not increment
        
        # Third call should also be deduplicated
        result3 = await test_func()
        assert result3 is None
        assert call_count == 1


@pytest.mark.asyncio
async def test_deduplicated_redis_error_fallback():
    """Test deduplicated falls back to execution on Redis error"""
    call_count = 0
    
    mock_redis = AsyncMock()
    mock_redis.set.side_effect = Exception("Redis connection error")
    
    @deduplicated(key="test_func", ttl=60)
    async def test_func():
        nonlocal call_count
        call_count += 1
        return "executed"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        # Should execute despite Redis error
        result = await test_func()
        assert result == "executed"
        assert call_count == 1


@pytest.mark.asyncio
async def test_deduplicated_custom_prefix():
    """Test deduplicated with custom prefix"""
    mock_redis = AsyncMock()
    mock_redis.set.return_value = True
    
    @deduplicated(key="test_func", ttl=60, prefix="custom_prefix")
    async def test_func():
        return "executed"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        await test_func()
        
        # Check that custom prefix was used
        call_args = mock_redis.set.call_args
        key = call_args[0][0]
        assert key == "custom_prefix:test_func"


@pytest.mark.asyncio
async def test_deduplicated_ttl():
    """Test deduplicated TTL parameter is passed correctly"""
    mock_redis = AsyncMock()
    mock_redis.set.return_value = True
    
    @deduplicated(key="test_func", ttl=300)
    async def test_func():
        return "executed"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        await test_func()
        
        # Check that TTL was passed
        call_args = mock_redis.set.call_args
        assert call_args[1]['ex'] == 300


@pytest.mark.asyncio
async def test_deduplicated_with_arguments():
    """Test deduplicated decorator with function arguments"""
    call_count = 0
    
    mock_redis = AsyncMock()
    mock_redis.set.return_value = True
    
    @deduplicated(key="test_func", ttl=60)
    async def test_func(a, b, c=None):
        nonlocal call_count
        call_count += 1
        return f"{a}-{b}-{c}"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        result = await test_func(1, 2, c=3)
        assert result == "1-2-3"
        assert call_count == 1


@pytest.mark.asyncio
async def test_deduplicated_preserves_function_metadata():
    """Test deduplicated preserves function name and docstring"""
    @deduplicated(key="test_func", ttl=60)
    async def test_func():
        """Test function docstring"""
        return "result"
    
    assert test_func.__name__ == "test_func"
    assert test_func.__doc__ == "Test function docstring"


@pytest.mark.asyncio
async def test_deduplicated_with_exception():
    """Test deduplicated when function raises exception"""
    call_count = 0
    
    mock_redis = AsyncMock()
    mock_redis.set.return_value = True
    
    @deduplicated(key="test_func", ttl=60)
    async def test_func():
        nonlocal call_count
        call_count += 1
        raise ValueError("Test error")
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        # Exception should propagate
        with pytest.raises(ValueError, match="Test error"):
            await test_func()
        
        assert call_count == 1


@pytest.mark.asyncio
async def test_deduplicated_multiple_functions():
    """Test deduplicated with multiple functions using different keys"""
    call_count_1 = 0
    call_count_2 = 0
    
    mock_redis = AsyncMock()
    # Both functions should execute (both keys are new)
    mock_redis.set.return_value = True
    
    @deduplicated(key="func1", ttl=60)
    async def func1():
        nonlocal call_count_1
        call_count_1 += 1
        return "func1"
    
    @deduplicated(key="func2", ttl=60)
    async def func2():
        nonlocal call_count_2
        call_count_2 += 1
        return "func2"
    
    with patch('monitoring.decorators.get_redis_adapter', return_value=mock_redis):
        result1 = await func1()
        result2 = await func2()
        
        assert result1 == "func1"
        assert result2 == "func2"
        assert call_count_1 == 1
        assert call_count_2 == 1