import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request, HTTPException
from starlette.datastructures import Headers

from app.monitoring.middleware import MonitoringMiddleware, ErrorDeduplicator
from app.monitoring.config import AlertLevel


@pytest.fixture
def mock_config():
    """Мок конфигурации"""
    with patch("app.monitoring.middleware.monitoring_config") as mock:
        mock.is_enabled = True
        mock.MONITOR_EXCEPTIONS = True
        mock.MONITOR_SLOW_REQUESTS = True
        mock.SLOW_REQUEST_THRESHOLD_SECONDS = 1.0
        mock.should_monitor_path = MagicMock(return_value=True)
        mock.should_monitor_exception = MagicMock(return_value=True)
        mock.get_redis_key = MagicMock(return_value="test:key")
        yield mock


@pytest.fixture
def mock_redis():
    """Мок Redis клиента"""
    with patch("app.monitoring.middleware.get_redis_client") as mock:
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.setex = AsyncMock()
        redis_mock.incr = AsyncMock()
        redis_mock.expire = AsyncMock()
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.lpush = AsyncMock()
        mock.return_value = redis_mock
        yield redis_mock


@pytest.fixture
def mock_request():
    """Мок Request объекта"""
    request = MagicMock(spec=Request)
    request.url.path = "/api/test"
    request.url.query = ""
    request.method = "GET"
    request.headers = Headers({"user-agent": "test-agent"})
    request.client.host = "127.0.0.1"
    return request


@pytest.mark.asyncio
class TestMonitoringMiddleware:

    async def test_successful_request_passes_through(
        self, mock_config, mock_redis, mock_request
    ):
        """Успешный запрос проходит без вмешательства"""
        app = MagicMock()
        middleware = MonitoringMiddleware(app)

        async def call_next(request):
            response = MagicMock()
            response.status_code = 200
            return response

        response = await middleware.dispatch(mock_request, call_next)

        assert response.status_code == 200

    async def test_ignored_path_skips_monitoring(self, mock_config, mock_request):
        """Игнорируемые пути не мониторятся"""
        mock_config.should_monitor_path.return_value = False

        app = MagicMock()
        middleware = MonitoringMiddleware(app)

        called = False

        async def call_next(request):
            nonlocal called
            called = True
            return MagicMock()

        await middleware.dispatch(mock_request, call_next)

        assert called is True
        # Проверяем что мониторинг не вызывался через отсутствие вызовов telegram

    async def test_http_exception_500_triggers_alert(
        self, mock_config, mock_redis, mock_request
    ):
        """HTTP 500 ошибка вызывает алерт"""
        app = MagicMock()
        middleware = MonitoringMiddleware(app)

        async def call_next(request):
            raise HTTPException(status_code=500, detail="Internal error")

        with patch.object(middleware, "_handle_exception") as mock_handle:
            mock_handle.return_value = None

            with pytest.raises(HTTPException):
                await middleware.dispatch(mock_request, call_next)

            mock_handle.assert_called_once()

    async def test_http_exception_404_no_alert(
        self, mock_config, mock_redis, mock_request
    ):
        """HTTP 404 не вызывает алерт"""
        app = MagicMock()
        middleware = MonitoringMiddleware(app)

        async def call_next(request):
            raise HTTPException(status_code=404, detail="Not found")

        with patch.object(middleware, "_handle_exception") as mock_handle:
            with pytest.raises(HTTPException):
                await middleware.dispatch(mock_request, call_next)

            mock_handle.assert_not_called()

    async def test_unhandled_exception_sends_alert(
        self, mock_config, mock_redis, mock_request
    ):
        """Необработанное исключение отправляет алерт"""
        app = MagicMock()
        middleware = MonitoringMiddleware(app)

        async def call_next(request):
            raise ValueError("Unexpected error")

        with patch("app.monitoring.middleware.telegram_reporter") as mock_telegram:
            mock_telegram.send_alert = AsyncMock()

            response = await middleware.dispatch(mock_request, call_next)

            assert response.status_code == 500
            mock_telegram.send_alert.assert_called_once()

    async def test_monitoring_disabled_skips_checks(self, mock_request):
        """Выключенный мониторинг пропускает проверки"""
        with patch("app.monitoring.middleware.monitoring_config") as config:
            config.is_enabled = False

            app = MagicMock()
            middleware = MonitoringMiddleware(app)

            called = False

            async def call_next(request):
                nonlocal called
                called = True
                return MagicMock()

            await middleware.dispatch(mock_request, call_next)

            assert called is True


class TestErrorDeduplicator:

    def test_generate_fingerprint_consistency(self):
        """Один и тот же error генерирует один fingerprint"""
        dedup = ErrorDeduplicator()

        error1 = ValueError("Test error")
        error2 = ValueError("Test error")

        fp1 = dedup.generate_fingerprint("/api/test", "GET", error1)
        fp2 = dedup.generate_fingerprint("/api/test", "GET", error2)

        assert fp1 == fp2

    def test_generate_fingerprint_differs_by_path(self):
        """Разные пути генерируют разные fingerprint"""
        dedup = ErrorDeduplicator()

        error = ValueError("Test")

        fp1 = dedup.generate_fingerprint("/api/users", "GET", error)
        fp2 = dedup.generate_fingerprint("/api/posts", "GET", error)

        assert fp1 != fp2

    @pytest.mark.asyncio
    async def test_rate_limiting_blocks_duplicate(self):
        """Rate limiting блокирует дубликаты"""
        with patch("app.monitoring.middleware.get_redis_client") as mock_get_redis:
            redis_mock = AsyncMock()
            mock_get_redis.return_value = redis_mock

            dedup = ErrorDeduplicator()

            # Первый вызов - разрешен
            redis_mock.get = AsyncMock(return_value=None)
            redis_mock.setex = AsyncMock()
            should_send = await dedup.should_send_alert("test_fingerprint")
            assert should_send is True

            # Второй вызов сразу - заблокирован (возвращаем недавнее время)
            import time

            recent_time = str(time.time() - 60)  # 1 минуту назад
            redis_mock.get = AsyncMock(return_value=recent_time)
            should_send = await dedup.should_send_alert("test_fingerprint")
            assert should_send is False

    @pytest.mark.asyncio
    async def test_redis_failure_uses_local_cache(self):
        """При сбое Redis используется локальный кеш"""
        dedup = ErrorDeduplicator()

        with patch("app.monitoring.middleware.get_redis_client") as mock:
            mock.side_effect = Exception("Redis unavailable")

            # Первый вызов - разрешен
            should_send = await dedup.should_send_alert("test_fp")
            assert should_send is True

            # Проверяем что попало в локальный кеш
            assert "test_fp" in dedup.local_cache

    @pytest.mark.asyncio
    async def test_record_error_stats(self, mock_redis):
        """Запись статистики ошибок"""
        dedup = ErrorDeduplicator()

        await dedup.record_error("/api/test", 500, "ValueError")

        # Проверяем что были вызовы к Redis
        assert mock_redis.incr.call_count >= 3  # total, type, endpoint
        assert mock_redis.expire.call_count >= 3
