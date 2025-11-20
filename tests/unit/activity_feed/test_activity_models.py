"""
Unit tests for Activity Feed Models.
Tests data integrity, relationships, and constraints.
"""

import pytest
from uuid import uuid4
from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.activity_feed.models import PendingActivity, Activity, DailyActivitySummary
from app.core.datetime_utils import utc_now


@pytest.mark.asyncio
class TestPendingActivityModel:
    """Test suite for PendingActivity model."""

    async def test_create_pending_activity(self, db_session, user, project):
        """Test creating a basic pending activity."""
        pending = PendingActivity(
            session_key=f"{user.id}:{project.id}:123",
            project_id=project.id,
            user_id=user.id,
            event_type="element.created",
            target_id=str(uuid4()),
            target_type="element",
            details={"element_name": "Test"},
        )
        db_session.add(pending)
        await db_session.commit()

        # Verify it exists
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.id == pending.id)
        )
        saved = result.scalar_one()
        assert saved.event_type == "element.created"

    async def test_pending_activity_session_key_index(self, db_session, user, project):
        """Test that session_key is indexed for efficient queries."""
        session_key = f"{user.id}:{project.id}:124"

        # Create multiple events with same session key
        for i in range(3):
            pending = PendingActivity(
                session_key=session_key,
                project_id=project.id,
                user_id=user.id,
                event_type="element.updated",
                target_id=str(uuid4()),
                target_type="element",
                details={"element_name": f"Test {i}"},
            )
            db_session.add(pending)

        await db_session.commit()

        # Query by session_key should be fast
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.session_key == session_key)
        )
        events = result.scalars().all()
        assert len(events) == 3

    async def test_pending_activity_details_json(self, db_session, user, project):
        """Test that details are properly stored as JSON."""
        complex_details = {
            "element_name": "Test",
            "changes": {"title": "New", "props": {"key": "value"}},
            "nested": {"level1": {"level2": [1, 2, 3]}},
        }

        pending = PendingActivity(
            session_key=f"{user.id}:{project.id}:125",
            project_id=project.id,
            user_id=user.id,
            event_type="element.updated",
            target_id=str(uuid4()),
            target_type="element",
            details=complex_details,
        )
        db_session.add(pending)
        await db_session.commit()

        # Retrieve and verify
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.id == pending.id)
        )
        saved = result.scalar_one()
        assert saved.details == complex_details

    async def test_pending_activity_cascade_delete(self, db_session, user, project):
        """Test that pending activities are deleted when project is deleted."""
        pending = PendingActivity(
            session_key=f"{user.id}:{project.id}:126",
            project_id=project.id,
            user_id=user.id,
            event_type="element.created",
            target_id=str(uuid4()),
            target_type="element",
            details={},
        )
        db_session.add(pending)
        await db_session.commit()
        pending_id = pending.id

        # Delete project
        await db_session.delete(project)
        await db_session.commit()

        # Pending should be gone
        result = await db_session.execute(
            select(PendingActivity).where(PendingActivity.id == pending_id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
class TestActivityModel:
    """Test suite for Activity model."""

    async def test_create_activity(self, db_session, user, project):
        """Test creating an aggregated activity."""
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="User created 3 elements",
            summary={"groups": [{"type": "elements_created", "count": 3}]},
            affected_folders=[],
            affected_elements=[uuid4(), uuid4()],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        # Verify
        result = await db_session.execute(
            select(Activity).where(Activity.id == activity.id)
        )
        saved = result.scalar_one()
        assert saved.title == "User created 3 elements"
        assert len(saved.affected_elements) == 2

    async def test_activity_summary_gin_index(self, db_session, user, project):
        """Test that summary field has GIN index for JSONB queries."""
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Test",
            summary={
                "groups": [
                    {"type": "elements_created", "count": 5},
                    {"type": "folders_created", "count": 2},
                ]
            },
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        # This query should use GIN index
        result = await db_session.execute(
            select(Activity).where(
                Activity.summary.op("@>")('{"groups": [{"type": "elements_created"}]}')
            )
        )
        found = result.scalar_one_or_none()
        assert found is not None

    async def test_activity_affected_arrays(self, db_session, user, project):
        """Test affected_folders and affected_elements arrays."""
        folder_ids = [uuid4() for _ in range(3)]
        element_ids = [uuid4() for _ in range(5)]

        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Multiple entities affected",
            summary={"groups": []},
            affected_folders=folder_ids,
            affected_elements=element_ids,
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        # Query using array operators
        result = await db_session.execute(
            select(Activity).where(Activity.affected_folders.contains([folder_ids[0]]))
        )
        found = result.scalar_one_or_none()
        assert found is not None
        assert len(found.affected_folders) == 3
        assert len(found.affected_elements) == 5

    async def test_activity_user_relationship(self, db_session, user, project):
        """Test relationship with User model."""
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Test",
            summary={"groups": []},
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        # Load with relationship
        result = await db_session.execute(
            select(Activity).where(Activity.id == activity.id)
        )
        loaded = result.scalar_one()

        # Access user through relationship
        assert loaded.user_id == user.id

    async def test_activity_project_cascade_delete(self, db_session, user, project):
        """Test that activities are deleted when project is deleted."""
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Test",
            summary={"groups": []},
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()
        activity_id = activity.id

        # Delete project
        await db_session.delete(project)
        await db_session.commit()

        # Activity should be gone
        result = await db_session.execute(
            select(Activity).where(Activity.id == activity_id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
class TestDailyActivitySummaryModel:
    """Test suite for DailyActivitySummary model."""

    async def test_create_daily_summary(self, db_session, user, project):
        """Test creating a daily summary entry."""
        summary = DailyActivitySummary(
            activity_date=date.today(),
            project_id=project.id,
            user_id=user.id,
            event_count=10,
        )
        db_session.add(summary)
        await db_session.commit()

        # Verify
        result = await db_session.execute(
            select(DailyActivitySummary).where(
                DailyActivitySummary.project_id == project.id,
                DailyActivitySummary.user_id == user.id,
                DailyActivitySummary.activity_date == date.today(),
            )
        )
        saved = result.scalar_one()
        assert saved.event_count == 10

    async def test_daily_summary_composite_key(self, db_session, user, project):
        """Test composite primary key constraint."""
        summary1 = DailyActivitySummary(
            activity_date=date.today(),
            project_id=project.id,
            user_id=user.id,
            event_count=5,
        )
        db_session.add(summary1)
        await db_session.commit()

        # Try to create duplicate
        summary2 = DailyActivitySummary(
            activity_date=date.today(),
            project_id=project.id,
            user_id=user.id,
            event_count=10,
        )
        db_session.add(summary2)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_daily_summary_different_dates(self, db_session, user, project):
        """Test that same user/project can have multiple dates."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        summary1 = DailyActivitySummary(
            activity_date=today, project_id=project.id, user_id=user.id, event_count=5
        )
        summary2 = DailyActivitySummary(
            activity_date=yesterday,
            project_id=project.id,
            user_id=user.id,
            event_count=3,
        )
        db_session.add_all([summary1, summary2])
        await db_session.commit()

        # Both should exist
        result = await db_session.execute(
            select(DailyActivitySummary).where(
                DailyActivitySummary.project_id == project.id,
                DailyActivitySummary.user_id == user.id,
            )
        )
        summaries = result.scalars().all()
        assert len(summaries) == 2

    async def test_daily_summary_cascade_delete(self, db_session, user, project):
        """Test cascade delete when project is removed."""
        summary = DailyActivitySummary(
            activity_date=date.today(),
            project_id=project.id,
            user_id=user.id,
            event_count=5,
        )
        db_session.add(summary)
        await db_session.commit()

        # Delete project
        await db_session.delete(project)
        await db_session.commit()

        # Summary should be gone
        result = await db_session.execute(
            select(DailyActivitySummary).where(
                DailyActivitySummary.project_id == project.id
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_daily_summary_query_by_date_range(self, db_session, user, project):
        """Test querying summaries by date range."""
        base_date = date.today()

        # Create summaries for 7 days
        for i in range(7):
            summary = DailyActivitySummary(
                activity_date=base_date - timedelta(days=i),
                project_id=project.id,
                user_id=user.id,
                event_count=i + 1,
            )
            db_session.add(summary)

        await db_session.commit()

        # Query last 5 days
        start_date = base_date - timedelta(days=4)
        result = await db_session.execute(
            select(DailyActivitySummary).where(
                DailyActivitySummary.project_id == project.id,
                DailyActivitySummary.activity_date >= start_date,
                DailyActivitySummary.activity_date <= base_date,
            )
        )
        summaries = result.scalars().all()
        assert len(summaries) == 5


@pytest.mark.asyncio
class TestModelConstraints:
    """Test model constraints and data integrity."""

    async def test_pending_activity_requires_session_key(
        self, db_session, user, project
    ):
        """Test that session_key is required."""
        pending = PendingActivity(
            session_key=None,  # Invalid
            project_id=project.id,
            user_id=user.id,
            event_type="test",
            target_id="test",
            target_type="test",
            details={},
        )
        db_session.add(pending)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_activity_requires_title(self, db_session, user, project):
        """Test that activity title is required."""
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title=None,  # Invalid
            summary={},
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)

        with pytest.raises(IntegrityError):
            await db_session.commit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
