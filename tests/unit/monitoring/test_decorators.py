import pytest
from unittest.mock import AsyncMock, patch

from app.monitoring.decorators import deduplicated


@pytest.fixture
def mock_redis():
    """Мок Redis клиента"""
    with patch("app.monitoring.decorators.get_redis_client") as mock:
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(return_value=True)
        mock.return_value = redis_mock
        yield redis_mock


@pytest.mark.asyncio
class TestDeduplicatedDecorator:

    async def test_first_execution_runs(self, mock_redis):
        """Первое выполнение проходит"""
        executed = False

        @deduplicated(key="test_key", ttl=60)
        async def test_func():
            nonlocal executed
            executed = True
            return "success"

        result = await test_func()

        assert executed is True
        assert result == "success"
        mock_redis.set.assert_called_once()

    async def test_duplicate_execution_skipped(self, mock_redis):
        """Повторное выполнение пропускается"""
        execution_count = 0

        @deduplicated(key="test_key", ttl=60)
        async def test_func():
            nonlocal execution_count
            execution_count += 1
            return "success"

        # Первое выполнение
        mock_redis.set = AsyncMock(return_value=True)
        result1 = await test_func()
        assert result1 == "success"
        assert execution_count == 1

        # Второе выполнение - пропускается
        mock_redis.set = AsyncMock(return_value=False)
        result2 = await test_func()
        assert result2 is None
        assert execution_count == 1  # Не увеличилось

    async def test_custom_prefix_used(self, mock_redis):
        """Используется кастомный префикс"""

        @deduplicated(key="test", ttl=60, prefix="custom:prefix")
        async def test_func():
            return "success"

        await test_func()

        call_args = mock_redis.set.call_args
        used_key = call_args[0][0]
        assert used_key.startswith("custom:prefix:")

    async def test_ttl_parameter_passed(self, mock_redis):
        """TTL передается корректно"""

        @deduplicated(key="test", ttl=120)
        async def test_func():
            return "success"

        await test_func()

        call_kwargs = mock_redis.set.call_args[1]
        assert call_kwargs["ex"] == 120

    async def test_redis_failure_allows_execution(self):
        """При сбое Redis выполнение разрешается"""
        with patch("app.monitoring.decorators.get_redis_client") as mock:
            mock.side_effect = Exception("Redis unavailable")

            executed = False

            @deduplicated(key="test", ttl=60)
            async def test_func():
                nonlocal executed
                executed = True
                return "success"

            result = await test_func()

            assert executed is True
            assert result == "success"

    async def test_preserves_function_metadata(self, mock_redis):
        """Декоратор сохраняет метаданные функции"""

        @deduplicated(key="test", ttl=60)
        async def test_func():
            """Test docstring"""
            return "success"

        assert test_func.__name__ == "test_func"
        assert test_func.__doc__ == "Test docstring"

    async def test_different_keys_allow_parallel_execution(self, mock_redis):
        """Разные ключи позволяют параллельное выполнение"""
        count_a = 0
        count_b = 0

        @deduplicated(key="key_a", ttl=60)
        async def func_a():
            nonlocal count_a
            count_a += 1
            return "a"

        @deduplicated(key="key_b", ttl=60)
        async def func_b():
            nonlocal count_b
            count_b += 1
            return "b"

        mock_redis.set = AsyncMock(return_value=True)

        await func_a()
        await func_b()

        assert count_a == 1
        assert count_b == 1
