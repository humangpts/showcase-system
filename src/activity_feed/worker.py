"""
Background worker task for activity feed aggregation.
"""

from app.core.queue import task
from app.activity_feed.services.aggregator import activity_aggregator
from app.core.database import async_session_maker


@task
async def process_activity_session(ctx, session_key: str, **kwargs):
    """
    Background task for processing and aggregating activity session.

    This task is automatically scheduled when events are recorded.
    It waits for ACTIVITY_SESSION_DURATION (default: 15 minutes)
    before aggregating events into a single Activity record.

    Args:
        ctx: ARQ context
        session_key: Unique session key for aggregation
        **kwargs: Additional ARQ parameters (_job_key, _defer_by, etc.)
    """
    async with async_session_maker() as session:
        await activity_aggregator.aggregate_session(session, session_key)
