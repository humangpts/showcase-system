import logging
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased
from fastapi import HTTPException

from app.filemanager.core import UnitOfWorkFactory
from app.filemanager.services.access_scope.enhanced_access_scope_service import (
    enhanced_access_scope_service,
)
from app.filemanager.services.common import permission_checker, ResourceType, Permission
from app.filemanager.models.folder import FolderBase
from app.filemanager.models.element import ElementBase

from app.activity_feed.models import Activity
from app.activity_feed.schemas import ActivityFeedResponse
from app.gallery.models import GalleryImage

logger = logging.getLogger(__name__)


class ActivityFeedService:

    async def _enrich_activities_with_image_urls(
        self, session: AsyncSession, activities: List[Activity]
    ) -> List[Activity]:
        """
        Находит события загрузки изображений и обогащает их поле 'summary' актуальными URL-ами.
        """
        image_ids_to_fetch = set()

        # 1. Собрать все ID изображений из событий
        for activity in activities:
            if not activity.summary or not isinstance(activity.summary, dict):
                continue
            for group in activity.summary.get("groups", []):
                if group.get("type") == "images_uploaded":
                    # Проверяем оба варианта структуры данных
                    if "items" in group and isinstance(group["items"], list):
                        for item in group["items"]:
                            if isinstance(item, dict) and "id" in item:
                                image_ids_to_fetch.add(item["id"])
                    elif "items_by_parent" in group and isinstance(
                        group["items_by_parent"], dict
                    ):
                        for parent_key, items in group["items_by_parent"].items():
                            for item in items:
                                if isinstance(item, dict) and "id" in item:
                                    image_ids_to_fetch.add(item["id"])

        if not image_ids_to_fetch:
            return activities  # Нет изображений для обогащения

        # 2. Сделать ОДИН запрос к БД для получения всех нужных картинок
        image_query = select(
            GalleryImage.id, GalleryImage.thumbnail_url, GalleryImage.url
        ).where(GalleryImage.id.in_(list(image_ids_to_fetch)))
        result = await session.execute(image_query)
        # Создаем удобный словарь для быстрого доступа: { 'image_id': {thumbnailUrl: '...', url: '...'} }
        images_map = {
            str(row.id): {"thumbnailUrl": row.thumbnail_url, "url": row.url}
            for row in result
        }

        # 3. Обогатить объекты 'summary' в 'activities'
        for activity in activities:
            if not activity.summary or not isinstance(activity.summary, dict):
                continue
            for group in activity.summary.get("groups", []):
                if group.get("type") == "images_uploaded":
                    # Обновляем items, если они есть
                    if "items" in group and isinstance(group["items"], list):
                        for item in group["items"]:
                            if isinstance(item, dict):
                                image_data = images_map.get(item.get("id"))  # type: ignore
                                if image_data:
                                    item["thumbnailUrl"] = image_data["thumbnailUrl"]
                                    item["url"] = image_data["url"]
                    # Обновляем items_by_parent, если они есть
                    elif "items_by_parent" in group and isinstance(
                        group["items_by_parent"], dict
                    ):
                        for parent_key, items in group["items_by_parent"].items():
                            for item in items:
                                if isinstance(item, dict):
                                    image_data = images_map.get(item.get("id"))  # type: ignore
                                    if image_data:
                                        item["thumbnailUrl"] = image_data[
                                            "thumbnailUrl"
                                        ]
                                        item["url"] = image_data["url"]

        return activities

    async def get_feed_for_project(
        self,
        session: AsyncSession,
        *,
        user_id: Optional[UUID],
        project_id: UUID,
        page: int,
        size: int,
    ) -> ActivityFeedResponse:
        async with UnitOfWorkFactory.create(session) as uow:
            await permission_checker.require_permission(
                ResourceType.PROJECT,
                project_id,
                user_id,
                Permission.READ,
                context={"uow": uow, "permission_checker": permission_checker},
            )

            # 1. Создаем CTE ОДИН РАЗ
            user_projects_cte = (
                enhanced_access_scope_service.get_user_accessible_projects_cte(user_id)
            )
            folder_perms_cte = enhanced_access_scope_service.get_folder_permissions_cte(
                user_id
            )

            # 2. Получаем подзапросы, ПЕРЕДАВАЯ в них уже созданные CTE
            accessible_elements_subquery = (
                enhanced_access_scope_service.get_accessible_element_ids(
                    uow.session,
                    user_id,
                    project_id,
                    user_projects_cte=user_projects_cte,
                    folder_perms_cte=folder_perms_cte,
                )
            )
            accessible_folders_subquery = (
                enhanced_access_scope_service.get_accessible_folder_ids(
                    uow.session,
                    user_id,
                    project_id,
                    user_projects_cte=user_projects_cte,
                    folder_perms_cte=folder_perms_cte,
                )
            )

            # 3. Собираем условия фильтрации.
            security_filters = [
                or_(
                    func.cardinality(Activity.affected_elements) == 0,
                    Activity.affected_elements.op("<@")(
                        select(
                            func.array_agg(accessible_elements_subquery.c.id)
                        ).scalar_subquery()
                    ),
                ),
                or_(
                    func.cardinality(Activity.affected_folders) == 0,
                    Activity.affected_folders.op("<@")(
                        select(
                            func.array_agg(accessible_folders_subquery.c.id)
                        ).scalar_subquery()
                    ),
                ),
            ]

            # 4. Применяем фильтры к запросам
            base_filters = [Activity.project_id == project_id]

            final_filters = and_(*base_filters, *security_filters)

            count_query = select(func.count(Activity.id)).where(final_filters)
            total = await uow.session.scalar(count_query) or 0

            if total == 0:
                return ActivityFeedResponse(
                    items=[], total=0, page=page, size=size, pages=0
                )

            query = (
                select(Activity)
                .where(final_filters)
                .order_by(Activity.ended_at.desc())
                .offset((page - 1) * size)
                .limit(size)
                .options(selectinload(Activity.user))
            )

            result = await uow.session.execute(query)
            items = list(result.scalars().unique().all())

            items = await self._enrich_activities_with_image_urls(uow.session, items)

            return ActivityFeedResponse(
                items=items,  # type: ignore
                total=total,
                page=page,
                size=size,
                pages=(total + size - 1) // size if total > 0 else 0,
            )

    async def get_feed_for_folder(
        self,
        session: AsyncSession,
        *,
        user_id: Optional[UUID],
        folder_id: UUID,
        page: int,
        size: int,
    ) -> ActivityFeedResponse:
        async with UnitOfWorkFactory.create(session) as uow:
            folder = await uow.session.get(FolderBase, folder_id)
            if not folder:
                raise HTTPException(status_code=404, detail="Folder not found")
            await permission_checker.require_permission(
                ResourceType.FOLDER,
                folder_id,
                user_id,
                Permission.READ,
                context={"uow": uow, "permission_checker": permission_checker},
            )

            all_folder_ids = await self._get_folder_and_subfolder_ids(
                uow.session, folder_id
            )

            count_query = select(func.count(Activity.id)).where(
                and_(
                    Activity.project_id == folder.project_id,
                    Activity.affected_folders.overlap(all_folder_ids),
                )
            )
            total = await uow.session.scalar(count_query) or 0

            if total == 0:
                return ActivityFeedResponse(
                    items=[], total=0, page=page, size=size, pages=0
                )

            query = (
                select(Activity)
                .where(
                    and_(
                        Activity.project_id == folder.project_id,
                        Activity.affected_folders.overlap(all_folder_ids),
                    )
                )
                .order_by(Activity.ended_at.desc())
                .offset((page - 1) * size)
                .limit(size)
                .options(selectinload(Activity.user))
            )

            result = await uow.session.execute(query)
            items = list(result.scalars().unique().all())

            items = await self._enrich_activities_with_image_urls(uow.session, items)

            return ActivityFeedResponse(
                items=items,  # type: ignore
                total=total,
                page=page,
                size=size,
                pages=(total + size - 1) // size if total > 0 else 0,
            )

    async def get_feed_for_element(
        self,
        session: AsyncSession,
        *,
        user_id: Optional[UUID],
        element_id: UUID,
        page: int,
        size: int,
    ) -> ActivityFeedResponse:
        async with UnitOfWorkFactory.create(session) as uow:
            element = await uow.session.get(ElementBase, element_id)
            if not element:
                raise HTTPException(status_code=404, detail="Element not found")
            await permission_checker.require_permission(
                ResourceType.ELEMENT,
                element_id,
                user_id,
                Permission.READ,
                context={"uow": uow, "permission_checker": permission_checker},
            )

            count_query = select(func.count(Activity.id)).where(
                and_(
                    Activity.project_id == element.project_id,
                    Activity.affected_elements.contains([element_id]),
                )
            )
            total = await uow.session.scalar(count_query) or 0

            if total == 0:
                return ActivityFeedResponse(
                    items=[], total=0, page=page, size=size, pages=0
                )

            query = (
                select(Activity)
                .where(
                    and_(
                        Activity.project_id == element.project_id,
                        Activity.affected_elements.contains([element_id]),
                    )
                )
                .order_by(Activity.ended_at.desc())
                .offset((page - 1) * size)
                .limit(size)
                .options(selectinload(Activity.user))
            )

            result = await uow.session.execute(query)
            items = list(result.scalars().unique().all())

            items = await self._enrich_activities_with_image_urls(uow.session, items)

            return ActivityFeedResponse(
                items=items,  # type: ignore
                total=total,
                page=page,
                size=size,
                pages=(total + size - 1) // size if total > 0 else 0,
            )

    async def _get_folder_and_subfolder_ids(
        self, session: AsyncSession, folder_id: UUID
    ) -> List[UUID]:
        """
        Рекурсивно получает ID всех дочерних папок.
        Возвращает список UUID (не строк!).
        """
        folder_cte = (
            select(FolderBase.id)
            .where(FolderBase.id == folder_id)
            .cte(name="folder_tree", recursive=True)
        )

        parent_alias = aliased(FolderBase)
        folder_cte = folder_cte.union_all(
            select(parent_alias.id).join(
                folder_cte, parent_alias.parent_id == folder_cte.c.id
            )
        )

        query = select(folder_cte.c.id)
        result = await session.execute(query)

        return list(result.scalars().all())

    async def _get_folder_and_subfolder_ids_str(
        self, session: AsyncSession, folder_id: UUID
    ) -> List[str]:
        """
        Рекурсивно получает ID всех дочерних папок и возвращает их как список строк.
        Использует рекурсивный CTE для эффективности.
        """
        folder_cte = (
            select(FolderBase.id)
            .where(FolderBase.id == folder_id)
            .cte(name="folder_tree", recursive=True)
        )

        parent_alias = aliased(FolderBase)
        folder_cte = folder_cte.union_all(
            select(parent_alias.id).join(
                folder_cte, parent_alias.parent_id == folder_cte.c.id
            )
        )

        query = select(folder_cte.c.id)
        result = await session.execute(query)

        return [str(uuid_val) for uuid_val in result.scalars().all()]


activity_feed_service = ActivityFeedService()
