"""
Unit tests for Activity Feed configuration.
"""

import pytest
from uuid import uuid4
from sqlalchemy import select
from unittest.mock import patch, AsyncMock

from app.activity_feed.config import ActivityFeedConfig


class TestActivityFeedConfig:
    """Test suite for activity feed configuration."""

    def test_default_categories_enabled(self):
        """Test that all categories are enabled when explicitly configured."""
        # Явно передаем набор, чтобы изолироваться от .env файла
        expected_categories = {
            "elements",
            "folders",
            "gallery",
            "announcements",
            "projects",
            "comments",
            "widgets",
        }
        config = ActivityFeedConfig(ACTIVITY_ENABLED_CATEGORIES=expected_categories)  # type: ignore

        assert config.is_category_enabled("elements")
        assert config.is_category_enabled("folders")
        assert config.is_category_enabled("gallery")
        assert config.is_category_enabled("announcements")
        assert config.is_category_enabled("projects")
        assert config.is_category_enabled("comments")
        assert config.is_category_enabled("widgets")

    def test_disable_comments_category(self):
        """Test disabling comments category."""
        config = ActivityFeedConfig(
            ACTIVITY_ENABLED_CATEGORIES={
                "elements",
                "folders",
                "gallery",
                "announcements",
                "projects",
                "widgets",
            },
        )

        assert not config.is_category_enabled("comments")
        assert config.is_category_enabled("elements")
        assert config.is_category_enabled("widgets")

    def test_disable_multiple_categories(self):
        """Test disabling multiple categories."""
        config = ActivityFeedConfig(ACTIVITY_ENABLED_CATEGORIES={"elements", "folders"})

        assert config.is_category_enabled("elements")
        assert config.is_category_enabled("folders")
        assert not config.is_category_enabled("comments")
        assert not config.is_category_enabled("widgets")
        assert not config.is_category_enabled("gallery")

    def test_event_type_mapping(self):
        """Test event type to category mapping."""
        # Явно передаем полный набор категорий
        config = ActivityFeedConfig(
            ACTIVITY_ENABLED_CATEGORIES={
                "elements",
                "folders",
                "gallery",
                "announcements",
                "projects",
                "comments",
                "widgets",
            }
        )

        assert config.is_event_type_enabled("element.created")
        assert config.is_event_type_enabled("folder.updated")
        assert config.is_event_type_enabled("comment.created")
        assert config.is_event_type_enabled("gallery.image.uploaded")
        assert config.is_event_type_enabled("imagemap.created")
        assert config.is_event_type_enabled("imagemap.updated")
        assert config.is_event_type_enabled("imagemap.deleted")

    def test_comment_events_disabled(self):
        """Test that comment events are disabled when category is off."""
        config = ActivityFeedConfig(
            ACTIVITY_ENABLED_CATEGORIES={"elements", "folders", "widgets"},
        )

        assert not config.is_event_type_enabled("comment.created")
        assert config.is_event_type_enabled("element.created")
        assert config.is_event_type_enabled("imagemap.created")

    def test_widget_events_disabled(self):
        """Test that widget events are disabled when category is off."""
        config = ActivityFeedConfig(
            ACTIVITY_ENABLED_CATEGORIES={"elements", "folders", "comments"},
        )

        assert not config.is_event_type_enabled("imagemap.created")
        assert not config.is_event_type_enabled("imagemap.updated")
        assert not config.is_event_type_enabled("imagemap.deleted")
        assert config.is_event_type_enabled("element.created")
        assert config.is_event_type_enabled("comment.created")

    def test_unknown_event_type_enabled_by_default(self):
        """Test that unknown event types are enabled by default."""
        config = ActivityFeedConfig()

        # Unknown event type should be enabled by default
        assert config.is_event_type_enabled("unknown.event.type")

    def test_session_duration_default(self):
        """Test default session duration."""
        config = ActivityFeedConfig()
        assert config.ACTIVITY_SESSION_DURATION == 900  # 15 minutes

    def test_max_events_per_session_default(self):
        """Test default max events per session."""
        config = ActivityFeedConfig()
        assert config.ACTIVITY_MAX_EVENTS_PER_SESSION == 100

    def test_custom_session_settings(self):
        """Test custom session configuration."""
        config = ActivityFeedConfig(
            ACTIVITY_SESSION_DURATION=600,  # 10 minutes
            ACTIVITY_MAX_EVENTS_PER_SESSION=50,
        )

        assert config.ACTIVITY_SESSION_DURATION == 600
        assert config.ACTIVITY_MAX_EVENTS_PER_SESSION == 50

    def test_category_name_mapping(self):
        """Test internal category name mapping."""
        config = ActivityFeedConfig()

        # Test that event prefixes map correctly
        assert config._get_category_for_event_type("element.created") == "elements"
        assert config._get_category_for_event_type("folder.updated") == "folders"
        assert config._get_category_for_event_type("comment.created") == "comments"
        assert (
            config._get_category_for_event_type("gallery.image.uploaded") == "gallery"
        )
        assert (
            config._get_category_for_event_type("announcement.created")
            == "announcements"
        )
        assert config._get_category_for_event_type("project.updated") == "projects"
        assert config._get_category_for_event_type("imagemap.created") == "widgets"

    def test_empty_categories_disables_all(self):
        """Test that empty category set disables all events."""
        config = ActivityFeedConfig(ACTIVITY_ENABLED_CATEGORIES=set())

        assert not config.is_category_enabled("elements")
        assert not config.is_category_enabled("comments")
        assert not config.is_category_enabled("widgets")
        assert not config.is_event_type_enabled("element.created")
        assert not config.is_event_type_enabled("comment.created")
        assert not config.is_event_type_enabled("imagemap.created")


@pytest.mark.asyncio
class TestActivityRecorder:
    """Test suite for activity recorder with configuration."""

    async def test_recorder_respects_disabled_category(self, db_session, user, project):
        """Test that recorder skips events from disabled categories."""
        from app.activity_feed.config import activity_config
        from app.activity_feed.services.recorder import activity_recorder
        from app.activity_feed.models import PendingActivity

        # Temporarily disable comments
        original_categories = activity_config.ACTIVITY_ENABLED_CATEGORIES.copy()
        activity_config.ACTIVITY_ENABLED_CATEGORIES.discard("comments")

        try:
            # Мокаем очередь, чтобы не было ошибки подключения к Redis,
            # хотя в этом тесте вызов очереди и не должен произойти (так как категория отключена),
            # но для безопасности лучше замокать.
            with patch(
                "app.activity_feed.services.recorder.queue_manager.enqueue",
                new_callable=AsyncMock,
            ):
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="comment.created",
                    target_id=str(uuid4()),
                    target_type="comment",
                    details={"text": "test"},
                )

            # Should not create pending activity
            result = await db_session.execute(
                select(PendingActivity).where(
                    PendingActivity.event_type == "comment.created"
                )
            )
            assert result.scalar_one_or_none() is None

        finally:
            # Restore original configuration
            activity_config.ACTIVITY_ENABLED_CATEGORIES = original_categories

    async def test_recorder_processes_enabled_category(self, db_session, user, project):
        """Test that recorder processes events from enabled categories."""
        from app.activity_feed.services.recorder import activity_recorder
        from app.activity_feed.models import PendingActivity

        # ВАЖНО: Мокаем queue_manager.enqueue, чтобы не пытаться подключиться к Redis
        # Путь для patch должен вести туда, где импортируется queue_manager
        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ) as mock_enqueue:

            # Record an element event (should be enabled)
            await activity_recorder.record(
                session=db_session,
                user_id=user.id,
                project_id=project.id,
                event_type="element.created",
                target_id=str(uuid4()),
                target_type="element",
                details={"element_name": "Test Element"},
            )

            # Проверяем, что запись сохранилась в БД
            await db_session.flush()  # Гарантируем отправку в БД

            # Should create pending activity
            result = await db_session.execute(
                select(PendingActivity).where(
                    PendingActivity.event_type == "element.created"
                )
            )
            pending = result.scalar_one_or_none()

            assert pending is not None
            assert pending.event_type == "element.created"

            # Проверяем, что была попытка отправки задачи в очередь
            assert mock_enqueue.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
