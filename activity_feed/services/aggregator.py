import logging
from uuid import UUID
from typing import List, Dict, Any
from collections import defaultdict
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.core.datetime_utils import utc_now
from app.core.config import settings
from app.activity_feed.models import PendingActivity, Activity, DailyActivitySummary
from app.users.models import User

logger = logging.getLogger(__name__)


class ActivityAggregator:

    async def aggregate_session(self, session: AsyncSession, session_key: str):
        """
        Main aggregation method that runs in a single transaction.
        """
        async with session.begin():
            # Use FOR UPDATE SKIP LOCKED for race condition protection
            stmt = (
                select(PendingActivity)
                .where(PendingActivity.session_key == session_key)
                .with_for_update(skip_locked=True)
                .order_by(PendingActivity.created_at)
            )
            result = await session.execute(stmt)
            pending_events: List[PendingActivity] = list(result.scalars().all())

            if not pending_events:
                logger.info(
                    f"No pending events found for session_key: {session_key}. Skipping."
                )
                return

            # Check session freshness
            last_event_time = pending_events[-1].created_at
            now = utc_now()

            time_since_last_event = (now - last_event_time).total_seconds()

            if time_since_last_event < settings.ACTIVITY_SESSION_DURATION_SECONDS:
                logger.info(
                    f"Session {session_key} is still active. Deferring aggregation."
                )
                return

            # Generate summary
            summary_data = await self._build_summary(session, pending_events)

            affected_folders, affected_elements = await self._extract_affected_entities(
                pending_events
            )

            # Create Activity record
            first_event = pending_events[0]

            new_activity = Activity(
                project_id=first_event.project_id,
                user_id=first_event.user_id,
                title=summary_data["title"],
                summary=summary_data["summary"],
                affected_folders=affected_folders,
                affected_elements=affected_elements,
                started_at=first_event.created_at,
                ended_at=last_event_time,
            )
            session.add(new_activity)

            # Update daily summary
            await self._update_daily_summary(
                session=session,
                project_id=first_event.project_id,
                user_id=first_event.user_id,
                activity_date=last_event_time.date(),
                events_count=len(pending_events),
            )

            # Delete processed events from buffer
            delete_stmt = delete(PendingActivity).where(
                PendingActivity.session_key == session_key
            )
            await session.execute(delete_stmt)

            logger.info(
                f"Successfully aggregated {len(pending_events)} events for session_key: {session_key}"
            )
            logger.info(
                f"Affected: {len(affected_folders)} folders, {len(affected_elements)} elements"
            )

    async def _extract_affected_entities(
        self, events: List[PendingActivity]
    ) -> tuple[List[UUID], List[UUID]]:
        """
        Extract all affected folders and elements from event list.
        Returns (affected_folders, affected_elements)
        """
        affected_folders = set()
        affected_elements = set()

        for event in events:
            # Process folder events
            if event.event_type in [
                "folder.created",
                "folder.updated",
                "folder.trashed",
            ]:
                try:
                    folder_id = UUID(event.target_id)
                    affected_folders.add(folder_id)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid folder UUID in event: {event.target_id}")

            # Process element events
            elif event.event_type in [
                "element.created",
                "element.updated",
                "element.trashed",
                "element.moved",
            ]:
                try:
                    element_id = UUID(event.target_id)
                    affected_elements.add(element_id)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid element UUID in event: {event.target_id}")

                # Also add folder where element is located
                folder_id = event.details.get("folder_id")
                if folder_id:
                    try:
                        affected_folders.add(UUID(folder_id))
                    except (ValueError, TypeError):
                        pass

            # Process comments
            elif event.event_type == "comment.created":
                parent_type = event.details.get("parent_type")
                parent_id = event.details.get("parent_id")

                if parent_id:
                    try:
                        parent_uuid = UUID(parent_id)
                        if parent_type == "element":
                            affected_elements.add(parent_uuid)
                        elif parent_type == "folder":
                            affected_folders.add(parent_uuid)
                    except (ValueError, TypeError):
                        pass

            # Process gallery images
            elif event.event_type == "gallery.image.uploaded":
                parent_type = event.details.get("parent_type")
                parent_id = event.details.get("parent_id")

                if parent_id:
                    try:
                        parent_uuid = UUID(parent_id)
                        if parent_type == "element":
                            affected_elements.add(parent_uuid)
                        elif parent_type == "folder":
                            affected_folders.add(parent_uuid)
                    except (ValueError, TypeError):
                        pass

            # Process imagemap widgets
            elif event.event_type in [
                "imagemap.created",
                "imagemap.updated",
                "imagemap.deleted",
            ]:
                entity_type = event.details.get("entity_type")
                entity_id = event.details.get("entity_id")

                if entity_id:
                    try:
                        entity_uuid = UUID(entity_id)
                        if entity_type == "element":
                            affected_elements.add(entity_uuid)
                        elif entity_type == "folder":
                            affected_folders.add(entity_uuid)
                    except (ValueError, TypeError):
                        pass

            # For element move, include both old and new folders
            if event.event_type == "element.moved":
                old_folder_id = event.details.get("old_folder_id")
                if old_folder_id:
                    try:
                        affected_folders.add(UUID(old_folder_id))
                    except (ValueError, TypeError):
                        pass

        return list(affected_folders), list(affected_elements)

    async def _update_daily_summary(
        self,
        session: AsyncSession,
        *,
        project_id: UUID,
        user_id: UUID,
        activity_date,
        events_count: int,
    ):
        """
        Atomically create or update (increment) counter in DailyActivitySummary.
        """
        stmt = (
            insert(DailyActivitySummary)
            .values(
                activity_date=activity_date,
                project_id=project_id,
                user_id=user_id,
                event_count=events_count,
            )
            .on_conflict_do_update(
                index_elements=["activity_date", "project_id", "user_id"],
                set_=dict(
                    event_count=DailyActivitySummary.event_count + events_count,
                    updated_at=utc_now(),
                ),
            )
        )
        await session.execute(stmt)
        logger.info(
            f"Updated daily summary for user {user_id} on {activity_date} by {events_count} events."
        )

    async def _build_summary(
        self, session: AsyncSession, events: List[PendingActivity]
    ) -> Dict[str, Any]:
        """
        Build summary from event list into title and structured data.
        """
        user_id = events[0].user_id
        user = await session.get(User, user_id)
        user_name = user.name if user and user.name else "Пользователь"

        # Generate smart title
        title = self._generate_title(user_name, events)

        # Group events logic
        created_elements = [e for e in events if e.event_type == "element.created"]
        updated_elements = {
            e.target_id: e for e in events if e.event_type == "element.updated"
        }

        created_folders = [e for e in events if e.event_type == "folder.created"]
        updated_folders = {
            e.target_id: e for e in events if e.event_type == "folder.updated"
        }

        created_comments = [e for e in events if e.event_type == "comment.created"]
        uploaded_images = [
            e for e in events if e.event_type == "gallery.image.uploaded"
        ]
        created_announcements = [
            e for e in events if e.event_type == "announcement.created"
        ]

        # ImageMap events
        created_imagemaps = [e for e in events if e.event_type == "imagemap.created"]
        updated_imagemaps = [e for e in events if e.event_type == "imagemap.updated"]
        deleted_imagemaps = [e for e in events if e.event_type == "imagemap.deleted"]

        # Build structured summary for frontend
        summary_groups = []

        if created_elements:
            summary_groups.append(
                {
                    "type": "elements_created",
                    "count": len(created_elements),
                    "items": [
                        {"id": e.target_id, "name": e.details.get("element_name")}
                        for e in created_elements
                    ],
                }
            )

        if updated_elements:
            summary_groups.append(
                {
                    "type": "elements_updated",
                    "count": len(updated_elements),
                    "items": [
                        {"id": e.target_id, "name": e.details.get("element_name")}
                        for e in updated_elements.values()
                    ],
                }
            )

        if created_folders:
            summary_groups.append(
                {
                    "type": "folders_created",
                    "count": len(created_folders),
                    "items": [
                        {"id": e.target_id, "name": e.details.get("folder_name")}
                        for e in created_folders
                    ],
                }
            )

        if updated_folders:
            summary_groups.append(
                {
                    "type": "folders_updated",
                    "count": len(updated_folders),
                    "items": [
                        {"id": e.target_id, "name": e.details.get("folder_name")}
                        for e in updated_folders.values()
                    ],
                }
            )

        if created_comments:
            summary_groups.append(
                {
                    "type": "comments_added",
                    "count": len(created_comments),
                    "items_by_parent": self._group_by_parent(created_comments),
                }
            )

        if uploaded_images:
            summary_groups.append(
                {
                    "type": "images_uploaded",
                    "count": len(uploaded_images),
                    "items_by_parent": self._group_by_parent(uploaded_images),
                }
            )

        if created_announcements:
            summary_groups.append(
                {
                    "type": "announcements_created",
                    "count": len(created_announcements),
                    "items": [
                        {"id": e.target_id, "name": e.details.get("title")}
                        for e in created_announcements
                    ],
                }
            )

        # ImageMap groups
        if created_imagemaps:
            summary_groups.append(
                {
                    "type": "widgets_created",
                    "count": len(created_imagemaps),
                    "items": [
                        {
                            "id": e.target_id,
                            "name": e.details.get("name"),
                            "entity_type": e.details.get("entity_type"),
                        }
                        for e in created_imagemaps
                    ],
                }
            )

        if updated_imagemaps:
            summary_groups.append(
                {
                    "type": "widgets_updated",
                    "count": len(updated_imagemaps),
                    "items": [
                        {
                            "id": e.target_id,
                            "name": e.details.get("name"),
                            "entity_type": e.details.get("entity_type"),
                        }
                        for e in updated_imagemaps
                    ],
                }
            )

        if deleted_imagemaps:
            summary_groups.append(
                {
                    "type": "widgets_deleted",
                    "count": len(deleted_imagemaps),
                    "items": [
                        {
                            "id": e.target_id,
                            "name": e.details.get("name"),
                            "entity_type": e.details.get("entity_type"),
                        }
                        for e in deleted_imagemaps
                    ],
                }
            )

        return {"title": title, "summary": {"groups": summary_groups}}

    def _generate_title(self, user_name: str, events: List[PendingActivity]) -> str:
        """Generate smart title based on event set."""

        # Group events by type
        event_counts: Dict[str, List[PendingActivity]] = defaultdict(list)
        for event in events:
            event_counts[event.event_type].append(event)

        # For single event
        if len(events) == 1:
            event = events[0]
            return self._single_event_title(user_name, event)

        # For multiple events of same type
        if len(event_counts) == 1:
            event_type = list(event_counts.keys())[0]
            events_of_type = event_counts[event_type]
            return self._same_type_events_title(user_name, event_type, events_of_type)

        # For mixed event types
        return self._mixed_events_title(user_name, event_counts, events)

    def _single_event_title(self, user_name: str, event: PendingActivity) -> str:
        """Generate title for single event."""
        details = event.details

        titles_map = {
            "element.created": f"{user_name} создал(а) элемент «{details.get('element_name', '...')}»",
            "element.updated": f"{user_name} обновил(а) элемент «{details.get('element_name', '...')}»",
            "element.trashed": f"{user_name} удалил(а) элемент «{details.get('element_name', '...')}»",
            "element.moved": f"{user_name} переместил(а) элемент «{details.get('element_name', '...')}»",
            "folder.created": f"{user_name} создал(а) папку «{details.get('folder_name', '...')}»",
            "folder.updated": f"{user_name} обновил(а) папку «{details.get('folder_name', '...')}»",
            "folder.trashed": f"{user_name} удалил(а) папку «{details.get('folder_name', '...')}»",
            "comment.created": f"{user_name} оставил(а) комментарий",
            "gallery.image.uploaded": f"{user_name} загрузил(а) изображение «{details.get('image_name', '...')}»",
            "announcement.created": f"{user_name} создал(а) задачу «{details.get('title', '...')}»",
            "announcement.updated": f"{user_name} обновил(а) задачу «{details.get('title', '...')}»",
            "project.updated": f"{user_name} обновил(а) проект «{details.get('project_name', '...')}»",
            "imagemap.created": f"{user_name} создал(а) виджет «{details.get('name', '...')}»",
            "imagemap.updated": f"{user_name} обновил(а) виджет «{details.get('name', '...')}»",
            "imagemap.deleted": f"{user_name} удалил(а) виджет «{details.get('name', '...')}»",
        }

        return titles_map.get(event.event_type, f"{user_name} выполнил(а) действие")

    def _same_type_events_title(
        self, user_name: str, event_type: str, events: List[PendingActivity]
    ) -> str:
        """Generate title for multiple events of same type."""
        count = len(events)

        titles_map = {
            "element.created": f"{user_name} создал(а) {self._plural_form(count, 'элемент', 'элемента', 'элементов')}",
            "element.updated": f"{user_name} обновил(а) {self._plural_form(count, 'элемент', 'элемента', 'элементов')}",
            "element.trashed": f"{user_name} удалил(а) {self._plural_form(count, 'элемент', 'элемента', 'элементов')}",
            "folder.created": f"{user_name} создал(а) {self._plural_form(count, 'папку', 'папки', 'папок')}",
            "folder.updated": f"{user_name} обновил(а) {self._plural_form(count, 'папку', 'папки', 'папок')}",
            "comment.created": f"{user_name} оставил(а) {self._plural_form(count, 'комментарий', 'комментария', 'комментариев')}",
            "gallery.image.uploaded": f"{user_name} загрузил(а) {self._plural_form(count, 'изображение', 'изображения', 'изображений')}",
            "announcement.created": f"{user_name} создал(а) {self._plural_form(count, 'задачу', 'задачи', 'задач')}",
            "imagemap.created": f"{user_name} создал(а) {self._plural_form(count, 'виджет', 'виджета', 'виджетов')}",
            "imagemap.updated": f"{user_name} обновил(а) {self._plural_form(count, 'виджет', 'виджета', 'виджетов')}",
            "imagemap.deleted": f"{user_name} удалил(а) {self._plural_form(count, 'виджет', 'виджета', 'виджетов')}",
        }

        return titles_map.get(event_type, f"{user_name} выполнил(а) {count} действий")

    def _mixed_events_title(
        self,
        user_name: str,
        event_counts: Dict[str, List[PendingActivity]],
        events: List[PendingActivity],
    ) -> str:
        """Generate title for mixed event types."""

        priority_actions = []

        # Content creation (high priority)
        created_types = []

        if "element.created" in event_counts:
            count = len(event_counts["element.created"])
            created_types.append(
                self._plural_form(count, "элемент", "элемента", "элементов")
            )

        if "folder.created" in event_counts:
            count = len(event_counts["folder.created"])
            created_types.append(self._plural_form(count, "папку", "папки", "папок"))

        if "imagemap.created" in event_counts:
            count = len(event_counts["imagemap.created"])
            created_types.append(
                self._plural_form(count, "виджет", "виджета", "виджетов")
            )

        if created_types:
            if len(created_types) == 1:
                priority_actions.append(f"создал(а) {created_types[0]}")
            else:
                priority_actions.append(f"создал(а) {' и '.join(created_types)}")

        # Updates (medium priority)
        updated_count = 0
        for event_type in [
            "element.updated",
            "folder.updated",
            "announcement.updated",
            "imagemap.updated",
        ]:
            if event_type in event_counts:
                updated_count += len(event_counts[event_type])

        if updated_count > 0:
            priority_actions.append(
                f"обновил(а) {self._plural_form(updated_count, 'объект', 'объекта', 'объектов')}"
            )

        # Comments and images (low priority)
        if "comment.created" in event_counts:
            count = len(event_counts["comment.created"])
            priority_actions.append(
                f"добавил(а) {self._plural_form(count, 'комментарий', 'комментария', 'комментариев')}"
            )

        if "gallery.image.uploaded" in event_counts:
            count = len(event_counts["gallery.image.uploaded"])
            priority_actions.append(
                f"загрузил(а) {self._plural_form(count, 'изображение', 'изображения', 'изображений')}"
            )

        # Build final title
        if priority_actions:
            # Take max 2 most important actions
            main_actions = priority_actions[:2]
            result = f"{user_name} {' и '.join(main_actions)}"

            # If more actions, add total count
            if len(priority_actions) > 2:
                extra_count = len(events) - sum(
                    len(evt_list) for evt_list in list(event_counts.values())[:2]
                )
                if extra_count > 0:
                    result += f" (+еще {extra_count} действий)"

            return result

        # Fallback for unknown event types
        return f"{user_name} выполнил(а) {len(events)} действий в проекте"

    def _plural_form(self, count: int, one: str, few: str, many: str) -> str:
        """Helper method for noun declension."""
        if count % 10 == 1 and count % 100 != 11:
            return f"{count} {one}"
        elif 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
            return f"{count} {few}"
        else:
            return f"{count} {many}"

    def _group_by_parent(
        self, events: List[PendingActivity]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Helper function to group by parent entity."""
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            parent_type = event.details.get("parent_type")
            parent_id = event.details.get("parent_id")
            if parent_type and parent_id:
                key = f"{parent_type}:{parent_id}"
                grouped[key].append(
                    {
                        "id": event.target_id,
                        "snippet": event.details.get("text_snippet")
                        or event.details.get("image_name"),
                    }
                )
        return dict(grouped)


activity_aggregator = ActivityAggregator()
