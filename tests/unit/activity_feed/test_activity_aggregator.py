"""
Unit tests for Activity Aggregator service.
Tests the core aggregation logic without Redis dependencies.
"""

import pytest
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select

from app.activity_feed.services.aggregator import activity_aggregator
from app.activity_feed.models import PendingActivity, Activity, DailyActivitySummary
from app.core.datetime_utils import utc_now


@pytest.mark.asyncio
class TestActivityAggregator:
    """Test suite for activity aggregation logic."""

    async def test_aggregate_single_event(self, db_session, user, project):
        """Test aggregation of a single event creates proper Activity."""
        session_key = f"{user.id}:{project.id}:123"

        # Create pending event
        pending = PendingActivity(
            session_key=session_key,
            user_id=user.id,
            project_id=project.id,
            event_type="element.created",
            target_id=str(uuid4()),
            target_type="element",
            details={"element_name": "Test Element"},
            created_at=utc_now() - timedelta(minutes=20),  # Old enough to aggregate
        )
        db_session.add(pending)
        await db_session.commit()

        # Run aggregation
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Check Activity was created
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        activity = result.scalar_one_or_none()

        assert activity is not None
        assert activity.user_id == user.id
        assert activity.project_id == project.id
        assert "создал(а) элемент" in activity.title.lower()
        assert len(activity.summary["groups"]) == 1
        assert activity.summary["groups"][0]["type"] == "elements_created"

    async def test_aggregate_multiple_elements_created(self, db_session, user, project):
        """Test aggregation of multiple element creation events."""
        session_key = f"{user.id}:{project.id}:124"
        created_time = utc_now() - timedelta(minutes=20)

        # Create 3 element creation events
        for i in range(3):
            pending = PendingActivity(
                session_key=session_key,
                user_id=user.id,
                project_id=project.id,
                event_type="element.created",
                target_id=str(uuid4()),
                target_type="element",
                details={"element_name": f"Element {i}"},
                created_at=created_time,
            )
            db_session.add(pending)

        await db_session.commit()

        # Aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Verify
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        activity = result.scalar_one()

        assert "3 элемента" in activity.title.lower()
        assert activity.summary["groups"][0]["count"] == 3

    async def test_aggregate_mixed_events(self, db_session, user, project):
        """Test aggregation of different event types."""
        session_key = f"{user.id}:{project.id}:125"
        created_time = utc_now() - timedelta(minutes=20)

        events = [
            ("element.created", "element", {"element_name": "Elem 1"}),
            ("element.created", "element", {"element_name": "Elem 2"}),
            ("folder.created", "folder", {"folder_name": "Folder 1"}),
            ("comment.created", "comment", {"text_snippet": "Nice work!"}),
        ]

        for event_type, target_type, details in events:
            pending = PendingActivity(
                session_key=session_key,
                user_id=user.id,
                project_id=project.id,
                event_type=event_type,
                target_id=str(uuid4()),
                target_type=target_type,
                details=details,
                created_at=created_time,
            )
            db_session.add(pending)

        await db_session.commit()

        # Aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Verify mixed activity
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        activity = result.scalar_one()

        assert len(activity.summary["groups"]) == 3  # elements, folders, comments
        group_types = {g["type"] for g in activity.summary["groups"]}
        assert "elements_created" in group_types
        assert "folders_created" in group_types
        assert "comments_added" in group_types

    async def test_aggregate_updates_daily_summary(self, db_session, user, project):
        """Test that aggregation updates DailyActivitySummary."""
        session_key = f"{user.id}:{project.id}:126"
        event_date = utc_now() - timedelta(minutes=20)

        # Create events
        for _ in range(5):
            pending = PendingActivity(
                session_key=session_key,
                user_id=user.id,
                project_id=project.id,
                event_type="element.updated",
                target_id=str(uuid4()),
                target_type="element",
                details={"element_name": "Test"},
                created_at=event_date,
            )
            db_session.add(pending)

        await db_session.commit()

        # Aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Check daily summary
        result = await db_session.execute(
            select(DailyActivitySummary).where(
                DailyActivitySummary.project_id == project.id,
                DailyActivitySummary.user_id == user.id,
                DailyActivitySummary.activity_date == event_date.date(),
            )
        )
        summary = result.scalar_one_or_none()

        assert summary is not None
        assert summary.event_count == 5

    async def test_aggregate_deletes_pending_events(self, db_session, user, project):
        """Test that aggregation removes processed pending events."""
        session_key = f"{user.id}:{project.id}:127"
        created_time = utc_now() - timedelta(minutes=20)

        pending = PendingActivity(
            session_key=session_key,
            user_id=user.id,
            project_id=project.id,
            event_type="folder.updated",
            target_id=str(uuid4()),
            target_type="folder",
            details={"folder_name": "Test"},
            created_at=created_time,
        )
        db_session.add(pending)
        await db_session.commit()

        # Aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Verify pending events deleted
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.session_key == session_key)
        )
        assert result.scalar_one_or_none() is None

    async def test_skip_aggregation_for_active_session(self, db_session, user, project):
        """Test that recent sessions are not aggregated yet."""
        session_key = f"{user.id}:{project.id}:128"

        # Create RECENT event (< 15 minutes ago)
        pending = PendingActivity(
            session_key=session_key,
            user_id=user.id,
            project_id=project.id,
            event_type="element.created",
            target_id=str(uuid4()),
            target_type="element",
            details={"element_name": "Recent"},
            created_at=utc_now() - timedelta(minutes=5),  # Too recent
        )
        db_session.add(pending)
        await db_session.commit()

        # Try to aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Should NOT create Activity
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        assert result.scalar_one_or_none() is None

        # Pending should still exist
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.session_key == session_key)
        )
        assert result.scalar_one_or_none() is not None

    async def test_extract_affected_folders_and_elements(
        self, db_session, user, project
    ):
        """Test extraction of affected entities from events."""
        session_key = f"{user.id}:{project.id}:129"
        created_time = utc_now() - timedelta(minutes=20)

        folder_id = uuid4()
        element_id = uuid4()

        events = [
            PendingActivity(
                session_key=session_key,
                user_id=user.id,
                project_id=project.id,
                event_type="folder.created",
                target_id=str(folder_id),
                target_type="folder",
                details={"folder_name": "Test Folder"},
                created_at=created_time,
            ),
            PendingActivity(
                session_key=session_key,
                user_id=user.id,
                project_id=project.id,
                event_type="element.created",
                target_id=str(element_id),
                target_type="element",
                details={"element_name": "Test Element", "folder_id": str(folder_id)},
                created_at=created_time,
            ),
        ]

        for event in events:
            db_session.add(event)
        await db_session.commit()

        # Aggregate
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Check affected entities
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        activity = result.scalar_one()

        assert folder_id in activity.affected_folders
        assert element_id in activity.affected_elements

    async def test_title_generation_single_event(self):
        """Test title generation for single event."""
        user_name = "John Doe"

        event = PendingActivity(
            session_key="test",
            user_id=uuid4(),
            project_id=uuid4(),
            event_type="element.created",
            target_id=str(uuid4()),
            target_type="element",
            details={"element_name": "My Element"},
            created_at=utc_now(),
        )

        title = activity_aggregator._single_event_title(user_name, event)

        assert user_name in title
        assert "создал(а) элемент" in title.lower()
        assert "My Element" in title

    async def test_title_generation_multiple_same_type(self):
        """Test title generation for multiple events of same type."""
        user_name = "Jane Smith"

        events = [
            PendingActivity(
                session_key="test",
                user_id=uuid4(),
                project_id=uuid4(),
                event_type="element.created",
                target_id=str(uuid4()),
                target_type="element",
                details={"element_name": f"Element {i}"},
                created_at=utc_now(),
            )
            for i in range(3)
        ]

        title = activity_aggregator._same_type_events_title(
            user_name, "element.created", events
        )

        assert user_name in title
        assert "3 элемента" in title.lower()

    async def test_no_events_returns_early(self, db_session, user, project):
        """Test that aggregation with no events exits gracefully."""
        session_key = f"{user.id}:{project.id}:nonexistent"

        # Should not raise error
        await activity_aggregator.aggregate_session(db_session, session_key)

        # No Activity should be created
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
class TestActivityAggregatorEdgeCases:
    """Test edge cases and error handling."""

    async def test_handle_invalid_uuid_in_details(self, db_session, user, project):
        """Test graceful handling of invalid UUIDs."""
        session_key = f"{user.id}:{project.id}:130"
        created_time = utc_now() - timedelta(minutes=20)

        pending = PendingActivity(
            session_key=session_key,
            user_id=user.id,
            project_id=project.id,
            event_type="element.created",
            target_id="not-a-uuid",  # Invalid UUID
            target_type="element",
            details={"element_name": "Test", "folder_id": "also-not-uuid"},
            created_at=created_time,
        )
        db_session.add(pending)
        await db_session.commit()

        # Should not crash
        await activity_aggregator.aggregate_session(db_session, session_key)

        # Activity should still be created
        result = await db_session.execute(
            select(Activity).where(Activity.project_id == project.id)
        )
        activity = result.scalar_one_or_none()
        assert activity is not None

    async def test_plural_forms_russian(self):
        """Test Russian plural form generation."""
        # Test element plurals
        assert "1 элемент" in activity_aggregator._plural_form(
            1, "элемент", "элемента", "элементов"
        )
        assert "2 элемента" in activity_aggregator._plural_form(
            2, "элемент", "элемента", "элементов"
        )
        assert "5 элементов" in activity_aggregator._plural_form(
            5, "элемент", "элемента", "элементов"
        )
        assert "21 элемент" in activity_aggregator._plural_form(
            21, "элемент", "элемента", "элементов"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
