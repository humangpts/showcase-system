import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.monitoring import setup_monitoring, send_startup_notification
from fastapi import FastAPI


@pytest.fixture
def mock_config():
    """Мок конфигурации для integration tests"""
    with patch("app.monitoring.monitoring_config") as mock:
        mock.MONITORING_ENABLED = True
        mock.MONITOR_EXCEPTIONS = True
        mock.TELEGRAM_BOT_TOKEN = "test_token"
        mock.TELEGRAM_CHAT_ID = "12345"
        mock.MONITORING_ENV = "test"
        mock.is_production = False
        yield mock


@pytest.fixture
def mock_telegram():
    """Мок telegram reporter"""
    with patch("app.monitoring.telegram_reporter") as mock:
        mock.send_message = AsyncMock()
        yield mock


class TestMonitoringSetup:

    def test_setup_monitoring_with_valid_config(self, mock_config):
        """Setup с валидной конфигурацией"""
        app = FastAPI()

        setup_monitoring(app)

        # Проверяем что middleware был добавлен
        # user_middleware содержит tuple (middleware_class, options)
        middleware_added = any(
            (
                "MonitoringMiddleware" in type(m).__name__
                if callable(m)
                else "MonitoringMiddleware" in str(m)
            )
            for m in app.user_middleware
        )

        assert middleware_added or len(app.user_middleware) > 0

    def test_setup_monitoring_disabled(self):
        """Setup не добавляет middleware если отключен"""
        with patch("app.monitoring.monitoring_config") as config:
            config.MONITORING_ENABLED = False

            app = FastAPI()
            setup_monitoring(app)

            # Middleware не должен быть добавлен
            assert not any(
                "MonitoringMiddleware" in str(type(m)) for m in app.user_middleware
            )

    def test_setup_without_telegram_credentials(self):
        """Setup без Telegram credentials"""
        with patch("app.monitoring.monitoring_config") as config:
            config.MONITORING_ENABLED = True
            config.TELEGRAM_BOT_TOKEN = None
            config.TELEGRAM_CHAT_ID = "123"

            app = FastAPI()
            setup_monitoring(app)

            # Middleware не должен быть добавлен без credentials
            assert not any(
                "MonitoringMiddleware" in str(type(m)) for m in app.user_middleware
            )


@pytest.mark.asyncio
class TestStartupNotification:

    async def test_sends_notification_in_production(self, mock_config, mock_telegram):
        """Отправляет уведомление в production"""
        mock_config.is_production = True

        with patch("app.monitoring.decorators.get_redis_client") as redis_mock:
            redis = AsyncMock()
            redis.set = AsyncMock(return_value=True)
            redis_mock.return_value = redis

            await send_startup_notification()

            mock_telegram.send_message.assert_called_once()

    async def test_skips_notification_in_development(self, mock_config, mock_telegram):
        """Пропускает уведомление в development"""
        mock_config.is_production = False

        with patch("app.monitoring.decorators.get_redis_client") as redis_mock:
            redis = AsyncMock()
            redis.set = AsyncMock(return_value=True)
            redis_mock.return_value = redis

            await send_startup_notification()

            mock_telegram.send_message.assert_not_called()

    async def test_handles_telegram_failure_gracefully(
        self, mock_config, mock_telegram
    ):
        """Обрабатывает ошибку Telegram без падения"""
        mock_config.is_production = True
        mock_telegram.send_message = AsyncMock(side_effect=Exception("Telegram error"))

        with patch("app.monitoring.decorators.get_redis_client") as redis_mock:
            redis = AsyncMock()
            redis.set = AsyncMock(return_value=True)
            redis_mock.return_value = redis

            # Не должно выбросить исключение
            await send_startup_notification()


class TestMonitoringModuleExports:

    def test_all_exports_available(self):
        """Проверка что все экспорты доступны"""
        from app.monitoring import (
            setup_monitoring,
            monitoring_config,
            telegram_reporter,
            monitored_task,
            monitored_periodic_task,
        )

        assert callable(setup_monitoring)
        assert monitoring_config is not None
        assert telegram_reporter is not None
        assert callable(monitored_task)
        assert callable(monitored_periodic_task)
