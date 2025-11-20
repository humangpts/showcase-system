"""
Activity Feed configuration module.
Manages which event types should be recorded in the activity feed.
"""

from typing import Set, Literal
from pydantic import Field
from pydantic_settings import BaseSettings


EventCategory = Literal[
    "elements",
    "folders",
    "gallery",
    "announcements",
    "projects",
    "comments",
    "widgets",
]


class ActivityFeedConfig(BaseSettings):
    """
    Configuration for activity feed module.
    Controls which types of events are recorded.
    """

    # Event categories that should be recorded
    ACTIVITY_ENABLED_CATEGORIES: Set[EventCategory] = Field(
        default={
            "elements",
            "folders",
            "gallery",
            "announcements",
            "projects",
            "comments",
            "widgets",
        },
        description="Set of event categories to record in activity feed",
    )

    # Session duration for event aggregation (in seconds)
    ACTIVITY_SESSION_DURATION: int = Field(
        default=900,  # 15 minutes
        description="Time window for aggregating related events",
    )

    # Maximum events in a single aggregated activity
    ACTIVITY_MAX_EVENTS_PER_SESSION: int = Field(
        default=100, description="Maximum number of events to aggregate in one activity"
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def is_category_enabled(self, category: EventCategory) -> bool:
        """Check if an event category is enabled."""
        return category in self.ACTIVITY_ENABLED_CATEGORIES

    def is_event_type_enabled(self, event_type: str) -> bool:
        """
        Check if a specific event type should be recorded.

        Args:
            event_type: Event type string (e.g., 'comment.created', 'element.updated')

        Returns:
            True if the event should be recorded, False otherwise
        """
        category = self._get_category_for_event_type(event_type)

        if not category:
            # Unknown event type, default to enabled
            return True

        return self.is_category_enabled(category)  # type: ignore[arg-type]

    def _get_category_for_event_type(self, event_type: str) -> str | None:
        """
        Map event type to category.

        Args:
            event_type: Event type string

        Returns:
            Category name or None if unknown
        """
        # Map event types to categories
        category_mapping = {
            "element": "elements",
            "folder": "folders",
            "gallery": "gallery",
            "announcement": "announcements",
            "project": "projects",
            "comment": "comments",
            "imagemap": "widgets",
        }

        # Extract category from event type (e.g., 'element.created' -> 'element')
        event_prefix = event_type.split(".")[0]

        return category_mapping.get(event_prefix)


activity_config = ActivityFeedConfig()
