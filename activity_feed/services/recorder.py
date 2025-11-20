import time
import logging
from typing import Dict, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.activity_feed.config import activity_config
from app.core.queue import queue_manager
from app.activity_feed.models import PendingActivity

logger = logging.getLogger(__name__)


class ActivityRecorder:
    """
    Records raw activity events to buffer before aggregation.
    Respects category configuration to filter events.
    """

    def _generate_session_key(self, user_id: UUID, project_id: UUID) -> str:
        """Generate session key based on user, project and time window."""
        timestamp_bucket = int(time.time() // activity_config.ACTIVITY_SESSION_DURATION)
        return f"{user_id}:{project_id}:{timestamp_bucket}"

    async def record(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        project_id: UUID,
        event_type: str,
        target_id: str,
        target_type: str,
        details: Dict[str, Any],
    ):
        """
        Record raw event to buffer if its category is enabled.

        Args:
            session: Database session
            user_id: User who performed the action
            project_id: Project where action occurred
            event_type: Type of event (e.g., 'element.created')
            target_id: ID of target entity
            target_type: Type of target entity
            details: Additional event details
        """
        # Check if event category is enabled
        if not activity_config.is_event_type_enabled(event_type):
            category = activity_config._get_category_for_event_type(event_type)
            logger.debug(
                f"Skipping event '{event_type}' (category: {category}) - "
                f"disabled in configuration"
            )
            return

        session_key = self._generate_session_key(user_id, project_id)

        pending_event = PendingActivity(
            session_key=session_key,
            user_id=user_id,
            project_id=project_id,
            event_type=event_type,
            target_id=str(target_id),
            target_type=target_type,
            details=details,
        )
        session.add(pending_event)

        # Schedule background aggregation task
        # _job_key ensures only one task per session is queued
        # New events will restart the timer for existing task
        await queue_manager.enqueue(
            "process_activity_session",
            session_key,
            _defer_by=activity_config.ACTIVITY_SESSION_DURATION,
            _job_key=f"activity_session:{session_key}",
        )

        logger.debug(
            f"Recorded event '{event_type}' for session {session_key[:20]}... "
            f"(user: {user_id}, project: {project_id})"
        )


# Singleton instance
activity_recorder = ActivityRecorder()
