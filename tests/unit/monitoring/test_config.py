import pytest
from unittest.mock import patch

from app.monitoring.config import (
    MonitoringConfig,
    AlertLevel,
    HealthCheckConfig,
    MetricsConfig,
)


class TestMonitoringConfig:

    def test_is_production_detection(self):
        """Определение production окружения"""
        config = MonitoringConfig(MONITORING_ENV="production")
        assert config.is_production is True

        config = MonitoringConfig(MONITORING_ENV="development")
        assert config.is_production is False

    def test_is_enabled_requires_all_params(self):
        """is_enabled требует все параметры"""
        # Все параметры присутствуют
        config = MonitoringConfig(
            MONITORING_ENABLED=True, TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="123"
        )
        assert config.is_enabled is True

        # Отсутствует токен
        config = MonitoringConfig(
            MONITORING_ENABLED=True, TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID="123"
        )
        assert config.is_enabled is False

        # Мониторинг выключен
        config = MonitoringConfig(
            MONITORING_ENABLED=False, TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="123"
        )
        assert config.is_enabled is False

    def test_should_monitor_exception(self):
        """Фильтрация исключений"""
        config = MonitoringConfig(
            IGNORED_EXCEPTIONS=["HTTPException", "ValidationError"]
        )

        assert config.should_monitor_exception("RuntimeError") is True
        assert config.should_monitor_exception("HTTPException") is False
        assert config.should_monitor_exception("ValidationError") is False

    def test_should_monitor_path(self):
        """Фильтрация путей"""
        config = MonitoringConfig(IGNORED_PATHS=["/health", "/metrics"])

        assert config.should_monitor_path("/api/users") is True
        assert config.should_monitor_path("/health") is False
        assert config.should_monitor_path("/health/check") is False
        assert config.should_monitor_path("/metrics") is False

    def test_get_redis_key_formatting(self):
        """Формирование Redis ключей"""
        config = MonitoringConfig(REDIS_KEY_PREFIX="monitoring")

        key = config.get_redis_key("errors", "2024-01-01", "total")
        assert key == "monitoring:errors:2024-01-01:total"

        key = config.get_redis_key("health")
        assert key == "monitoring:health"


class TestHealthCheckConfig:

    def test_all_checks_enabled_by_default(self):
        """Все проверки включены по умолчанию"""
        config = HealthCheckConfig()

        assert config.check_database is True
        assert config.check_redis is True
        assert config.check_disk is True
        assert config.check_memory is True
        assert config.check_queue is True

    def test_custom_thresholds(self):
        """Пользовательские пороги"""
        config = HealthCheckConfig(disk_critical=95, memory_critical=85)

        assert config.disk_critical == 95
        assert config.memory_critical == 85


class TestMetricsConfig:

    def test_default_collection_settings(self):
        """Дефолтные настройки сбора метрик"""
        config = MetricsConfig()

        assert config.collect_user_metrics is True
        assert config.collect_project_metrics is True
        assert config.collect_error_metrics is True
        assert config.collect_performance_metrics is False  # Phase 2

    def test_report_settings(self):
        """Настройки отчетов"""
        config = MetricsConfig()

        assert config.report_new_users is True
        assert config.report_errors_summary is True
        assert config.report_slow_queries is False  # Phase 2


class TestAlertLevel:

    def test_alert_levels_exist(self):
        """Проверка наличия уровней алертов"""
        assert AlertLevel.CRITICAL == "critical"
        assert AlertLevel.WARNING == "warning"
        assert AlertLevel.INFO == "info"
