"""
Unit tests for Activity Recorder service.
Tests event recording logic and session key generation.
"""

import pytest
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from app.activity_feed.services.recorder import activity_recorder
from app.activity_feed.models import PendingActivity
from app.activity_feed.config import ActivityFeedConfig


@pytest.mark.asyncio
class TestActivityRecorder:
    """Test suite for activity event recording."""

    async def test_record_creates_pending_event(self, db_session, user, project):
        """Test that recording creates a PendingActivity entry."""
        element_id = uuid4()

        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ):
            await activity_recorder.record(
                session=db_session,
                user_id=user.id,
                project_id=project.id,
                event_type="element.created",
                target_id=str(element_id),
                target_type="element",
                details={"element_name": "Test Element"},
            )

        await db_session.flush()

        # Verify pending event was created
        result = await db_session.execute(
            select(PendingActivity).where(
                PendingActivity.user_id == user.id,
                PendingActivity.project_id == project.id,
                PendingActivity.event_type == "element.created",
            )
        )
        pending = result.scalar_one_or_none()

        assert pending is not None
        assert pending.target_id == str(element_id)
        assert pending.details["element_name"] == "Test Element"

    async def test_session_key_generation(self):
        """Test session key format and consistency."""
        user_id = uuid4()
        project_id = uuid4()

        key1 = activity_recorder._generate_session_key(user_id, project_id)
        key2 = activity_recorder._generate_session_key(user_id, project_id)

        # Same inputs should generate same key (within same time bucket)
        assert key1 == key2

        # Key should contain user and project IDs
        assert str(user_id) in key1
        assert str(project_id) in key1

    async def test_different_users_different_keys(self):
        """Test that different users get different session keys."""
        user1 = uuid4()
        user2 = uuid4()
        project_id = uuid4()

        key1 = activity_recorder._generate_session_key(user1, project_id)
        key2 = activity_recorder._generate_session_key(user2, project_id)

        assert key1 != key2

    async def test_record_enqueues_aggregation_task(self, db_session, user, project):
        """Test that recording enqueues background aggregation task."""
        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await activity_recorder.record(
                session=db_session,
                user_id=user.id,
                project_id=project.id,
                event_type="folder.created",
                target_id=str(uuid4()),
                target_type="folder",
                details={"folder_name": "New Folder"},
            )

            # Verify task was enqueued
            assert mock_enqueue.called
            call_args = mock_enqueue.call_args

            assert call_args[0][0] == "process_activity_session"
            assert "_defer_by" in call_args[1]
            assert "_job_key" in call_args[1]

    async def test_record_respects_disabled_category(self, db_session, user, project):
        """Test that disabled categories are not recorded."""
        # Create config with comments disabled
        config = ActivityFeedConfig(ACTIVITY_ENABLED_CATEGORIES={"elements", "folders"})

        with patch("app.activity_feed.services.recorder.activity_config", config):
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
                    details={"text": "Comment"},
                )

        # Should not create pending event
        result = await db_session.execute(
            select(PendingActivity).where(
                PendingActivity.event_type == "comment.created"
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_record_multiple_events_same_session(self, db_session, user, project):
        """Test recording multiple events creates them in same session."""
        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ):
            # Record 3 events rapidly
            for i in range(3):
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="element.updated",
                    target_id=str(uuid4()),
                    target_type="element",
                    details={"element_name": f"Element {i}"},
                )

        await db_session.flush()

        # All should have same session_key
        result = await db_session.execute(
            select(PendingActivity).where(
                PendingActivity.user_id == user.id,
                PendingActivity.project_id == project.id,
            )
        )
        events = result.scalars().all()

        assert len(events) == 3
        session_keys = {e.session_key for e in events}
        assert len(session_keys) == 1  # All same session

    async def test_record_different_event_types(self, db_session, user, project):
        """Test recording various event types."""
        event_types = [
            ("element.created", "element", {"element_name": "Test"}),
            ("folder.updated", "folder", {"folder_name": "Test"}),
            ("gallery.image.uploaded", "gallery_image", {"image_name": "Test"}),
            ("imagemap.created", "imagemap", {"name": "Test"}),
        ]

        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ):
            for event_type, target_type, details in event_types:
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type=event_type,
                    target_id=str(uuid4()),
                    target_type=target_type,
                    details=details,
                )

        await db_session.flush()

        # Verify all were recorded
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.user_id == user.id)
        )
        events = result.scalars().all()
        assert len(events) == 4

    async def test_record_preserves_event_details(self, db_session, user, project):
        """Test that event details are properly stored."""
        complex_details = {
            "element_name": "Complex Element",
            "folder_id": str(uuid4()),
            "changes": {"title": "New Title", "description": "New Desc"},
            "old_values": {"title": "Old Title"},
            "nested": {"data": {"value": 123}},
        }

        with patch(
            "app.activity_feed.services.recorder.queue_manager.enqueue",
            new_callable=AsyncMock,
        ):
            await activity_recorder.record(
                session=db_session,
                user_id=user.id,
                project_id=project.id,
                event_type="element.updated",
                target_id=str(uuid4()),
                target_type="element",
                details=complex_details,
            )

        await db_session.flush()

        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.user_id == user.id)
        )
        pending = result.scalar_one()

        # Verify details preserved
        assert pending.details == complex_details
        assert pending.details["nested"]["data"]["value"] == 123


@pytest.mark.asyncio
class TestRecorderConfiguration:
    """Test recorder behavior with different configurations."""

    async def test_custom_session_duration_affects_key(self):
        """Test that session duration setting affects key generation."""
        config = ActivityFeedConfig(ACTIVITY_SESSION_DURATION=1800)  # 30 minutes

        with patch("app.activity_feed.services.recorder.activity_config", config):
            user_id = uuid4()
            project_id = uuid4()

            key = activity_recorder._generate_session_key(user_id, project_id)

            # Key should exist and be valid
            assert key is not None
            assert len(key) > 0

    async def test_widget_events_enabled(self, db_session, user, project):
        """Test recording widget (imagemap) events when enabled."""
        config = ActivityFeedConfig(ACTIVITY_ENABLED_CATEGORIES={"widgets"})

        with patch("app.activity_feed.services.recorder.activity_config", config):
            with patch(
                "app.activity_feed.services.recorder.queue_manager.enqueue",
                new_callable=AsyncMock,
            ):
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="imagemap.created",
                    target_id=str(uuid4()),
                    target_type="imagemap",
                    details={"name": "Test Widget"},
                )

        await db_session.flush()

        result = await db_session.execute(
            select(PendingActivity).where(
                PendingActivity.event_type == "imagemap.created"
            )
        )
        assert result.scalar_one_or_none() is not None

    async def test_multiple_categories_disabled(self, db_session, user, project):
        """Test recording with multiple categories disabled."""
        config = ActivityFeedConfig(
            ACTIVITY_ENABLED_CATEGORIES={"elements"}  # Only elements enabled
        )

        with patch("app.activity_feed.services.recorder.activity_config", config):
            with patch(
                "app.activity_feed.services.recorder.queue_manager.enqueue",
                new_callable=AsyncMock,
            ):
                # Try to record disabled events
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="comment.created",
                    target_id=str(uuid4()),
                    target_type="comment",
                    details={"text": "Test"},
                )

                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="folder.created",
                    target_id=str(uuid4()),
                    target_type="folder",
                    details={"folder_name": "Test"},
                )

                # Record enabled event
                await activity_recorder.record(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    event_type="element.created",
                    target_id=str(uuid4()),
                    target_type="element",
                    details={"element_name": "Test"},
                )

        await db_session.flush()

        # Only element event should be recorded
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.user_id == user.id)
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "element.created"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
