import logging
from typing import Optional
from uuid import UUID
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.filemanager.core import UnitOfWorkFactory
from app.activity_feed.models import DailyActivitySummary
from app.activity_feed.schemas import ActivityHeatmapResponse, ActivityHeatmapItem
from app.filemanager.services.common import permission_checker, ResourceType, Permission

logger = logging.getLogger(__name__)

class ActivityHeatmapService:
    """
    Сервис для получения данных для аналитических виджетов,
    таких как heatmap-календарь.
    """

    async def get_heatmap_for_project(
        self,
        session: AsyncSession,
        *,
        user_id: Optional[UUID],
        project_id: UUID,
        start_date: date,
        end_date: date,
        filter_user_id: Optional[UUID] = None
    ) -> ActivityHeatmapResponse:
        """
        Получает данные для heatmap-календаря из сводной таблицы.
        """
        async with UnitOfWorkFactory.create(session) as uow:
            await permission_checker.require_permission(
                ResourceType.PROJECT, project_id, user_id, Permission.READ, context={'uow': uow, 'permission_checker': permission_checker}
            )

            query = (
                select(
                    DailyActivitySummary.activity_date.label("date"),
                    func.sum(DailyActivitySummary.event_count).label("count")
                )
                .where(
                    DailyActivitySummary.project_id == project_id,
                    DailyActivitySummary.activity_date >= start_date,
                    DailyActivitySummary.activity_date <= end_date,
                )
                .group_by(DailyActivitySummary.activity_date)
                .order_by(DailyActivitySummary.activity_date)
            )

            if filter_user_id:
                query = query.where(DailyActivitySummary.user_id == filter_user_id)
            
            result = await uow.session.execute(query)
            items = [ActivityHeatmapItem(date=d, count=c) for d, c in result]
        
        return ActivityHeatmapResponse(items=items)

# Синглтон-экземпляр сервиса
activity_heatmap_service = ActivityHeatmapService()
