import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.activity_feed.services.recorder import activity_recorder

from app.projects.models import Project
from app.comments.models import Comment
from app.gallery.models import GalleryImage
from app.announcements.models import Announcement
from app.filemanager.models.element import ElementBase
from app.filemanager.models.folder import FolderBase
from app.imagemap.models import ImageMap

logger = logging.getLogger(__name__)


# ===== Project Events =====


@subscribe("project.updated")
async def handle_project_updated(
    session: AsyncSession, user_id: UUID, project: Project, changes: dict, **kwargs
):
    """Handle project update event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=project.id,
        event_type="project.updated",
        target_id=str(project.id),
        target_type="project",
        details={"project_name": project.name, "changes": changes},
    )


# ===== Element Events =====


@subscribe("element.created")
async def handle_element_created(
    session: AsyncSession, user_id: UUID, element: ElementBase, **kwargs
):
    """Handle element creation event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=element.project_id,
        event_type="element.created",
        target_id=str(element.id),
        target_type="element",
        details={
            "element_name": element.name,
            "element_type_id": element.type_id,
            "folder_id": str(element.folder_id) if element.folder_id else None,
        },
    )


@subscribe("element.updated")
async def handle_element_updated(
    session: AsyncSession,
    user_id: UUID,
    element: ElementBase,
    changes: dict,
    old_values: dict,
    **kwargs,
):
    """Handle element update event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=element.project_id,
        event_type="element.updated",
        target_id=str(element.id),
        target_type="element",
        details={
            "element_name": element.name,
            "folder_id": str(element.folder_id) if element.folder_id else None,
            "changes": changes,
            "old_values": old_values,
        },
    )


@subscribe("element.trashed")
async def handle_element_trashed(
    session: AsyncSession, user_id: UUID, element: ElementBase, **kwargs
):
    """Handle element trash event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=element.project_id,
        event_type="element.trashed",
        target_id=str(element.id),
        target_type="element",
        details={
            "element_name": element.name,
            "folder_id": str(element.folder_id) if element.folder_id else None,
        },
    )


@subscribe("element.moved")
async def handle_element_moved(
    session: AsyncSession,
    user_id: UUID,
    element: ElementBase,
    old_folder_id: Optional[UUID],
    **kwargs,
):
    """Handle element move event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=element.project_id,
        event_type="element.moved",
        target_id=str(element.id),
        target_type="element",
        details={
            "element_name": element.name,
            "new_folder_id": str(element.folder_id) if element.folder_id else None,
            "old_folder_id": str(old_folder_id) if old_folder_id else None,
        },
    )


# ===== Folder Events =====


@subscribe("folder.created")
async def handle_folder_created(
    session: AsyncSession, user_id: UUID, folder: FolderBase, **kwargs
):
    """Handle folder creation event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=folder.project_id,
        event_type="folder.created",
        target_id=str(folder.id),
        target_type="folder",
        details={
            "folder_name": folder.name,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
        },
    )


@subscribe("folder.updated")
async def handle_folder_updated(
    session: AsyncSession,
    user_id: UUID,
    folder: FolderBase,
    changes: dict,
    old_values: dict,
    **kwargs,
):
    """Handle folder update event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=folder.project_id,
        event_type="folder.updated",
        target_id=str(folder.id),
        target_type="folder",
        details={
            "folder_name": folder.name,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
            "changes": changes,
            "old_values": old_values,
        },
    )


@subscribe("folder.trashed")
async def handle_folder_trashed(
    session: AsyncSession, user_id: UUID, folder: FolderBase, **kwargs
):
    """Handle folder trash event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=folder.project_id,
        event_type="folder.trashed",
        target_id=str(folder.id),
        target_type="folder",
        details={
            "folder_name": folder.name,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
        },
    )


# ===== Comment Events =====


@subscribe("comment.created")
async def handle_comment_created(
    session: AsyncSession, user_id: UUID, comment: Comment, **kwargs
):
    """Handle comment creation event."""
    if comment.entity_type not in ["element", "folder"]:
        return

    project_id = None
    if comment.entity_type == "element":
        q = await session.execute(
            select(ElementBase.project_id).where(ElementBase.id == comment.entity_id)
        )
        project_id = q.scalar_one_or_none()
    elif comment.entity_type == "folder":
        q = await session.execute(
            select(FolderBase.project_id).where(FolderBase.id == comment.entity_id)
        )
        project_id = q.scalar_one_or_none()

    if project_id:
        await activity_recorder.record(
            session=session,
            user_id=user_id,
            project_id=project_id,
            event_type="comment.created",
            target_id=str(comment.id),
            target_type="comment",
            details={
                "parent_id": str(comment.entity_id),
                "parent_type": comment.entity_type,
                "text_snippet": (
                    (comment.text[:75] + "...")
                    if len(comment.text) > 75
                    else comment.text
                ),
            },
        )
    else:
        logger.warning(
            f"Could not find project_id for comment on {comment.entity_type}:{comment.entity_id}"
        )


# ===== Gallery Events =====


@subscribe("gallery.image.uploaded")
async def handle_image_uploaded(
    session: AsyncSession, user_id: UUID, image: GalleryImage, **kwargs
):
    """Handle gallery image upload event."""
    if image.entity_type not in ["element", "folder"]:
        return

    project_id = None
    if image.entity_type == "element":
        q = await session.execute(
            select(ElementBase.project_id).where(ElementBase.id == image.entity_id)
        )
        project_id = q.scalar_one_or_none()
    elif image.entity_type == "folder":
        q = await session.execute(
            select(FolderBase.project_id).where(FolderBase.id == image.entity_id)
        )
        project_id = q.scalar_one_or_none()

    if project_id:
        await activity_recorder.record(
            session=session,
            user_id=user_id,
            project_id=project_id,
            event_type="gallery.image.uploaded",
            target_id=str(image.id),
            target_type="gallery_image",
            details={
                "image_name": image.name,
                "parent_id": str(image.entity_id),
                "parent_type": image.entity_type,
            },
        )
    else:
        logger.warning(
            f"Could not find project_id for gallery image on {image.entity_type}:{image.entity_id}"
        )


# ===== Announcement Events =====


@subscribe("announcement.created")
async def handle_announcement_created(
    session: AsyncSession, user_id: UUID, announcement: Announcement, **kwargs
):
    """Handle announcement creation event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=announcement.project_id,
        event_type="announcement.created",
        target_id=str(announcement.id),
        target_type=f"announcement-{announcement.category.value}",
        details={"title": announcement.title, "category": announcement.category.value},
    )


@subscribe("announcement.updated")
async def handle_announcement_updated(
    session: AsyncSession, user_id: UUID, announcement: Announcement, **kwargs
):
    """Handle announcement update event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=announcement.project_id,
        event_type="announcement.updated",
        target_id=str(announcement.id),
        target_type=f"announcement-{announcement.category.value}",
        details={"title": announcement.title, "category": announcement.category.value},
    )


@subscribe("announcement.deleted")
async def handle_announcement_deleted(
    session: AsyncSession, user_id: UUID, announcement: Announcement, **kwargs
):
    """Handle announcement deletion event."""
    await activity_recorder.record(
        session=session,
        user_id=user_id,
        project_id=announcement.project_id,
        event_type="announcement.deleted",
        target_id=str(announcement.id),
        target_type=f"announcement-{announcement.category.value}",
        details={"title": announcement.title, "category": announcement.category.value},
    )


# ===== ImageMap (Widget) Events =====


@subscribe("imagemap.created")
async def handle_imagemap_created(
    session: AsyncSession, user_id: UUID, imagemap: ImageMap, **kwargs
):
    """Handle imagemap (widget) creation event."""

    project_id = await _get_project_id_for_imagemap(session, imagemap)

    if project_id:
        await activity_recorder.record(
            session=session,
            user_id=user_id,
            project_id=project_id,
            event_type="imagemap.created",
            target_id=str(imagemap.id),
            target_type="imagemap",
            details={
                "name": imagemap.name,
                "entity_type": imagemap.entity_type,
                "entity_id": imagemap.entity_id,
            },
        )
    else:
        logger.warning(
            f"Could not find project_id for imagemap on {imagemap.entity_type}:{imagemap.entity_id}"
        )


@subscribe("imagemap.updated")
async def handle_imagemap_updated(
    session: AsyncSession, user_id: UUID, imagemap: ImageMap, **kwargs
):
    """Handle imagemap (widget) update event."""
    project_id = await _get_project_id_for_imagemap(session, imagemap)

    if project_id:
        await activity_recorder.record(
            session=session,
            user_id=user_id,
            project_id=project_id,
            event_type="imagemap.updated",
            target_id=str(imagemap.id),
            target_type="imagemap",
            details={
                "name": imagemap.name,
                "entity_type": imagemap.entity_type,
                "entity_id": imagemap.entity_id,
            },
        )
    else:
        logger.warning(
            f"Could not find project_id for imagemap on {imagemap.entity_type}:{imagemap.entity_id}"
        )


@subscribe("imagemap.deleted")
async def handle_imagemap_deleted(
    session: AsyncSession, user_id: UUID, imagemap: ImageMap, **kwargs
):
    """Handle imagemap (widget) deletion event."""
    project_id = await _get_project_id_for_imagemap(session, imagemap)

    if project_id:
        await activity_recorder.record(
            session=session,
            user_id=user_id,
            project_id=project_id,
            event_type="imagemap.deleted",
            target_id=str(imagemap.id),
            target_type="imagemap",
            details={
                "name": imagemap.name,
                "entity_type": imagemap.entity_type,
                "entity_id": imagemap.entity_id,
            },
        )
    else:
        logger.warning(
            f"Could not find project_id for imagemap on {imagemap.entity_type}:{imagemap.entity_id}"
        )


# ===== Helper Functions =====


async def _get_project_id_for_imagemap(
    session: AsyncSession, imagemap: ImageMap
) -> Optional[UUID]:
    """
    Get project_id for imagemap based on its entity_type.

    Args:
        session: Database session
        imagemap: ImageMap instance

    Returns:
        Project UUID or None if not found
    """
    try:
        entity_uuid = UUID(imagemap.entity_id)
    except (ValueError, TypeError):
        logger.error(f"Invalid entity_id for imagemap: {imagemap.entity_id}")
        return None

    project_id = None

    if imagemap.entity_type == "project":
        project_id = entity_uuid

    elif imagemap.entity_type == "element":
        q = await session.execute(
            select(ElementBase.project_id).where(ElementBase.id == entity_uuid)
        )
        project_id = q.scalar_one_or_none()

    elif imagemap.entity_type == "folder":
        q = await session.execute(
            select(FolderBase.project_id).where(FolderBase.id == entity_uuid)
        )
        project_id = q.scalar_one_or_none()

    return project_id
