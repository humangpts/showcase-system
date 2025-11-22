import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from app.monitoring.telegram import TelegramReporter, MessageFormat, AlertLevel
from app.monitoring.config import MonitoringConfig


@pytest.fixture
def mock_config():
    """Мок конфигурации с валидными данными"""
    with patch("app.monitoring.telegram.monitoring_config") as mock:
        mock.is_enabled = True
        mock.TELEGRAM_BOT_TOKEN = "test_token"
        mock.TELEGRAM_CHAT_ID = "12345"
        mock.TELEGRAM_THREAD_ID = None
        mock.ALERT_MAX_MESSAGE_LENGTH = 4000
        mock.ALERT_MAX_TRACEBACK_LINES = 15
        mock.MONITORING_ENV = "test"
        yield mock


@pytest.fixture
def reporter(mock_config):
    """Инстанс репортера с мок-конфигом"""
    return TelegramReporter()


@pytest.mark.asyncio
class TestTelegramReporter:

    async def test_send_message_success(self, reporter):
        """Успешная отправка сообщения"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await reporter.send_message(
                text="Test message", level=AlertLevel.INFO
            )

            assert result is True
            mock_client.post.assert_called_once()

    async def test_send_message_truncates_long_text(self, reporter, mock_config):
        """Длинное сообщение обрезается"""
        long_text = "x" * 5000

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            await reporter.send_message(text=long_text)

            # Проверяем что текст был обрезан
            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"]
            assert len(sent_text) <= mock_config.ALERT_MAX_MESSAGE_LENGTH

    async def test_send_message_disabled_monitoring(self, reporter):
        """Не отправляет если мониторинг выключен"""
        with patch("app.monitoring.telegram.monitoring_config") as mock_config:
            mock_config.is_enabled = False

            result = await reporter.send_message("test")

            assert result is False

    async def test_send_alert_formats_correctly(self, reporter):
        """Проверка форматирования алерта"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            await reporter.send_alert(
                title="Test Alert",
                message="Test message",
                level=AlertLevel.WARNING,
                details={"key": "value"},
                error=ValueError("test error"),
            )

            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"]

            assert "Test Alert" in sent_text
            assert "Test message" in sent_text
            assert "key" in sent_text
            assert "ValueError" in sent_text

    async def test_send_health_alert_with_failures(self, reporter):
        """Отправка health alert с ошибками"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await reporter.send_health_alert(
                components={"Database": True, "Redis": False},
                errors=["Redis connection failed"],
            )

            assert result is True
            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"]

            assert "Database" in sent_text
            assert "Redis" in sent_text
            assert "Redis connection failed" in sent_text

    async def test_send_daily_report_includes_stats(self, reporter):
        """Daily report включает статистику"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = MagicMock()

        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            stats = {
                "users": {"new": 10, "active": 50, "total": 100},
                "errors": {"total": 5},
            }

            result = await reporter.send_daily_report(stats)

            assert result is True
            call_args = mock_client.post.call_args
            sent_text = call_args[1]["json"]["text"]

            assert "10" in sent_text  # new users
            assert "50" in sent_text  # active users
            assert "5" in sent_text  # errors

    async def test_http_error_handling(self, reporter):
        """Обработка HTTP ошибок"""
        with patch.object(reporter, "client") as mock_client:
            mock_client.post = AsyncMock(side_effect=Exception("Network error"))

            result = await reporter.send_message("test")

            assert result is False

    async def test_context_manager_initializes_client(self, reporter):
        """Context manager инициализирует клиент"""
        async with reporter:
            assert reporter.client is not None

        assert reporter.client is None  # Закрыт после выхода
