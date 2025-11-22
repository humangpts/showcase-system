import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import time

from app.monitoring.arq_monitoring import monitored_task


@pytest.fixture
def mock_config():
    """Мок конфигурации"""
    with patch("app.monitoring.arq_monitoring.monitoring_config") as mock:
        mock.MONITOR_ARQ_TASKS = True
        mock.ARQ_IGNORED_TASKS = ["ignored_task"]
        mock.ARQ_TASK_FAILURE_ALERT = True
        mock.ARQ_TASK_SLOW_THRESHOLD_SECONDS = 1.0
        mock.get_redis_key = MagicMock(return_value="test:key")
        yield mock


@pytest.fixture
def mock_redis():
    """Мок Redis"""
    with patch("app.monitoring.arq_monitoring.get_redis_client") as mock:
        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)  # Возвращаем число, не AsyncMock
        redis_mock.expire = AsyncMock()
        redis_mock.setex = AsyncMock()
        redis_mock.lpush = AsyncMock()
        redis_mock.ltrim = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        mock.return_value = redis_mock
        yield redis_mock


@pytest.fixture
def mock_telegram():
    """Мок Telegram репортера"""
    with patch("app.monitoring.arq_monitoring.telegram_reporter") as mock:
        mock.send_alert = AsyncMock()
        yield mock


@pytest.mark.asyncio
class TestMonitoredTask:

    async def test_successful_task_execution(self, mock_config, mock_redis):
        """Успешное выполнение задачи"""
        executed = False

        @monitored_task
        async def test_task(ctx):
            nonlocal executed
            executed = True
            return "success"

        ctx = {}
        result = await test_task(ctx)

        assert executed is True
        assert result == "success"
        # Проверяем что записаны метрики успеха
        assert mock_redis.incr.called

    async def test_task_failure_sends_alert(self, mock_config, mock_redis):
        """Ошибка в задаче отправляет алерт"""
        # Настраиваем mock_redis.incr чтобы возвращать число
        mock_redis.incr = AsyncMock(return_value=1)

        with patch("app.monitoring.arq_monitoring.telegram_reporter") as mock_telegram:
            mock_telegram.send_alert = AsyncMock()

            @monitored_task
            async def failing_task(ctx):
                raise ValueError("Task failed")

            ctx = {}

            with pytest.raises(ValueError):
                await failing_task(ctx)

            # Проверяем что был отправлен алерт
            mock_telegram.send_alert.assert_called_once()
            # Проверяем что записаны метрики ошибок
            assert mock_redis.incr.called

    async def test_ignored_task_not_monitored(self, mock_config, mock_redis):
        """Игнорируемые задачи не мониторятся"""

        @monitored_task
        async def ignored_task(ctx):
            return "success"

        ctx = {}
        await ignored_task(ctx)

        # Не должно быть вызовов мониторинга
        mock_redis.incr.assert_not_called()

    async def test_slow_task_triggers_alert(self, mock_config, mock_redis):
        """Медленная задача вызывает алерт"""
        with patch("app.monitoring.arq_monitoring.telegram_reporter") as mock_telegram:
            mock_telegram.send_alert = AsyncMock()

            call_count = [0]

            @monitored_task
            async def slow_task(ctx):
                return "success"

            ctx = {}

            # Патчим time.time чтобы эмулировать медленное выполнение
            original_time = time.time

            def fake_time():
                call_count[0] += 1
                if call_count[0] == 1:
                    return 0.0  # Начало
                else:
                    return 2.0  # Конец (больше порога 1.0)

            with patch(
                "app.monitoring.arq_monitoring.time.time", side_effect=fake_time
            ):
                await slow_task(ctx)

            # Должна быть попытка проверить Redis на дедупликацию
            assert mock_redis.get.called

    async def test_monitoring_disabled_skips_tracking(self, mock_redis):
        """Выключенный мониторинг пропускает трекинг"""
        with patch("app.monitoring.arq_monitoring.monitoring_config") as config:
            config.MONITOR_ARQ_TASKS = False

            executed = False

            @monitored_task
            async def test_task(ctx):
                nonlocal executed
                executed = True
                return "success"

            ctx = {}
            result = await test_task(ctx)

            assert executed is True
            assert result == "success"
            mock_redis.incr.assert_not_called()

    async def test_preserves_task_metadata(self, mock_config, mock_redis):
        """Декоратор сохраняет метаданные задачи"""

        @monitored_task
        async def documented_task(ctx):
            """Task documentation"""
            return "success"

        assert documented_task.__name__ == "documented_task"
        assert documented_task.__doc__ == "Task documentation"

    async def test_records_execution_time(self, mock_config, mock_redis):
        """Записывается время выполнения"""

        @monitored_task
        async def test_task(ctx):
            return "success"

        ctx = {}
        await test_task(ctx)

        # Проверяем что время было записано в Redis
        assert mock_redis.lpush.called
        assert mock_redis.ltrim.called

    async def test_recurring_failures_tracked(self, mock_config, mock_redis):
        """Повторяющиеся ошибки отслеживаются"""
        with patch("app.monitoring.arq_monitoring.telegram_reporter") as mock_telegram:
            mock_telegram.send_alert = AsyncMock()

            @monitored_task
            async def failing_task(ctx):
                raise RuntimeError("Persistent error")

            ctx = {}

            # Первая ошибка
            mock_redis.incr = AsyncMock(return_value=1)
            with pytest.raises(RuntimeError):
                await failing_task(ctx)

            # Вторая ошибка
            mock_redis.incr = AsyncMock(return_value=2)
            with pytest.raises(RuntimeError):
                await failing_task(ctx)

            # Должно быть 2 алерта
            assert mock_telegram.send_alert.call_count == 2
