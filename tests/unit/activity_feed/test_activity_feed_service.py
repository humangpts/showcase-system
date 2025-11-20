"""
Unit tests for Activity Feed Service.
Tests feed retrieval, pagination, and permission filtering.
"""

import pytest
from uuid import uuid4
from datetime import datetime, timedelta
from sqlalchemy import select

from app.activity_feed.services.feed_service import activity_feed_service
from app.activity_feed.models import Activity
from app.filemanager.models.folder import FolderBase
from app.filemanager.models.element import ElementBase
from app.core.datetime_utils import utc_now


@pytest.fixture
async def folder_with_elements(db_session, user, project):
    """Create a folder with elements for testing."""
    folder = FolderBase(
        id=uuid4(),
        project_id=project.id,
        name="Test Folder",
        slug="test-folder",
        created_by=user.id,
        created_at=utc_now(),
    )
    db_session.add(folder)

    elements = []
    for i in range(3):
        element = ElementBase(
            id=uuid4(),
            project_id=project.id,
            folder_id=folder.id,
            type_id=1,  # Assuming type exists
            name=f"Element {i}",
            slug=f"element-{i}",
            created_by=user.id,
            created_at=utc_now(),
        )
        elements.append(element)
        db_session.add(element)

    await db_session.commit()
    return folder, elements


@pytest.fixture
async def sample_activities(db_session, user, project):
    """Create sample activities for testing."""
    activities = []
    base_time = utc_now()

    for i in range(5):
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title=f"Test Activity {i}",
            summary={"groups": [{"type": "elements_created", "count": 1}]},
            affected_folders=[],
            affected_elements=[],
            started_at=base_time - timedelta(hours=i),
            ended_at=base_time - timedelta(hours=i) + timedelta(minutes=5),
        )
        activities.append(activity)
        db_session.add(activity)

    await db_session.commit()
    return activities


@pytest.mark.asyncio
class TestActivityFeedService:
    """Test suite for activity feed retrieval."""

    async def test_get_feed_for_project_basic(
        self, db_session, user, project, sample_activities
    ):
        """Test basic project feed retrieval."""
        from unittest.mock import patch, AsyncMock

        # Mock permission checker
        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            # Mock access scope service to return all elements/folders
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                response = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=20,
                )

        assert response.total == 5
        assert len(response.items) == 5
        assert response.page == 1
        assert response.pages == 1

    async def test_get_feed_pagination(
        self, db_session, user, project, sample_activities
    ):
        """Test feed pagination works correctly."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                # Get first page
                page1 = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=2,
                )

                assert len(page1.items) == 2
                assert page1.total == 5
                assert page1.pages == 3

                # Get second page
                page2 = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=2,
                    size=2,
                )

                assert len(page2.items) == 2
                # Items should be different
                page1_ids = {item.id for item in page1.items}
                page2_ids = {item.id for item in page2.items}
                assert page1_ids.isdisjoint(page2_ids)

    async def test_get_feed_ordered_by_time(
        self, db_session, user, project, sample_activities
    ):
        """Test that activities are ordered by ended_at DESC."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                response = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=20,
                )

        # Should be ordered newest first
        for i in range(len(response.items) - 1):
            assert response.items[i].ended_at >= response.items[i + 1].ended_at

    async def test_get_feed_empty_project(self, db_session, user, project):
        """Test feed for project with no activities."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                response = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=20,
                )

        assert response.total == 0
        assert len(response.items) == 0
        assert response.pages == 0

    async def test_get_feed_for_folder(
        self, db_session, user, project, folder_with_elements
    ):
        """Test getting feed filtered by folder."""
        from unittest.mock import patch, AsyncMock

        folder, elements = folder_with_elements

        # Create activity affecting this folder
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Folder Activity",
            summary={"groups": []},
            affected_folders=[folder.id],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            response = await activity_feed_service.get_feed_for_folder(
                session=db_session,
                user_id=user.id,
                folder_id=folder.id,
                page=1,
                size=20,
            )

        assert response.total >= 1

    async def test_get_feed_for_element(
        self, db_session, user, project, folder_with_elements
    ):
        """Test getting feed filtered by element."""
        from unittest.mock import patch, AsyncMock

        folder, elements = folder_with_elements
        element = elements[0]

        # Create activity affecting this element
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Element Activity",
            summary={"groups": []},
            affected_folders=[],
            affected_elements=[element.id],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            response = await activity_feed_service.get_feed_for_element(
                session=db_session,
                user_id=user.id,
                element_id=element.id,
                page=1,
                size=20,
            )

        assert response.total >= 1

    async def test_get_feed_includes_user_info(
        self, db_session, user, project, sample_activities
    ):
        """Test that feed items include user information."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                response = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=20,
                )

        # Check first item has user
        first_item = response.items[0]
        assert first_item.user is not None
        assert first_item.user.id == user.id

    async def test_feed_respects_affected_entities(self, db_session, user, project):
        """Test that feed filtering by affected entities works."""
        from unittest.mock import patch, AsyncMock

        folder_id = uuid4()
        element_id = uuid4()

        # Activity 1: affects folder
        activity1 = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Activity 1",
            summary={"groups": []},
            affected_folders=[folder_id],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )

        # Activity 2: affects element
        activity2 = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Activity 2",
            summary={"groups": []},
            affected_folders=[],
            affected_elements=[element_id],
            started_at=utc_now(),
            ended_at=utc_now(),
        )

        # Activity 3: affects nothing specific
        activity3 = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Activity 3",
            summary={"groups": []},
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )

        db_session.add_all([activity1, activity2, activity3])
        await db_session.commit()

        with patch(
            "app.activity_feed.services.feed_service.permission_checker.require_permission",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.activity_feed.services.feed_service.enhanced_access_scope_service"
            ):
                response = await activity_feed_service.get_feed_for_project(
                    session=db_session,
                    user_id=user.id,
                    project_id=project.id,
                    page=1,
                    size=20,
                )

        # All three should be in project feed
        assert response.total >= 3


@pytest.mark.asyncio
class TestFeedServiceHelpers:
    """Test helper methods in feed service."""

    async def test_get_folder_and_subfolder_ids(self, db_session, user, project):
        """Test recursive folder ID retrieval."""
        # Create folder hierarchy
        parent = FolderBase(
            id=uuid4(),
            project_id=project.id,
            name="Parent",
            slug="parent",
            created_by=user.id,
        )
        db_session.add(parent)
        await db_session.flush()

        child = FolderBase(
            id=uuid4(),
            project_id=project.id,
            parent_id=parent.id,
            name="Child",
            slug="child",
            created_by=user.id,
        )
        db_session.add(child)
        await db_session.flush()

        grandchild = FolderBase(
            id=uuid4(),
            project_id=project.id,
            parent_id=child.id,
            name="Grandchild",
            slug="grandchild",
            created_by=user.id,
        )
        db_session.add(grandchild)
        await db_session.commit()

        # Get all folder IDs
        folder_ids = await activity_feed_service._get_folder_and_subfolder_ids(
            db_session, parent.id
        )

        # Should include parent and all descendants
        assert parent.id in folder_ids
        assert child.id in folder_ids
        assert grandchild.id in folder_ids
        assert len(folder_ids) == 3

    async def test_enrich_with_image_urls(self, db_session, user, project):
        """Test image URL enrichment for gallery events."""
        from app.gallery.models import GalleryImage

        # Create gallery image
        image = GalleryImage(
            id=uuid4(),
            entity_type="element",
            entity_id=uuid4(),
            name="test.jpg",
            url="https://example.com/test.jpg",
            thumbnail_url="https://example.com/thumb.jpg",
            uploaded_by=user.id,
        )
        db_session.add(image)
        await db_session.commit()

        # Create activity with image
        activity = Activity(
            project_id=project.id,
            user_id=user.id,
            title="Image uploaded",
            summary={
                "groups": [
                    {
                        "type": "images_uploaded",
                        "count": 1,
                        "items": [{"id": str(image.id), "name": "test.jpg"}],
                    }
                ]
            },
            affected_folders=[],
            affected_elements=[],
            started_at=utc_now(),
            ended_at=utc_now(),
        )
        db_session.add(activity)
        await db_session.commit()

        # Enrich
        enriched = await activity_feed_service._enrich_activities_with_image_urls(
            db_session, [activity]
        )

        # Check URLs added
        image_item = enriched[0].summary["groups"][0]["items"][0]
        assert "thumbnailUrl" in image_item
        assert image_item["thumbnailUrl"] == "https://example.com/thumb.jpg"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
