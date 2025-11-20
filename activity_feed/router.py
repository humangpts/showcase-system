from fastapi import APIRouter, Depends, Query, Path, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import Optional
from uuid import UUID

from app.core.dependencies import get_async_session
from app.auth.dependencies import current_optional_user
from app.users.models import User
from app.activity_feed.schemas import ActivityFeedResponse, ActivityHeatmapResponse
from app.activity_feed.services.feed_service import activity_feed_service
from app.activity_feed.services.heatmap_service import activity_heatmap_service

router = APIRouter(prefix="/feed", tags=["Activity Feed"])


@router.get("/project/{project_id}", response_model=ActivityFeedResponse)
async def get_project_feed(
    project_id: UUID = Path(..., description="ID проекта"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    size: int = Query(20, ge=1, le=100, description="Элементов на странице"),
    session: AsyncSession = Depends(get_async_session),
    current_user: Optional[User] = Depends(current_optional_user),
):
    """Получить ленту активности для всего проекта."""
    user_id = current_user.id if current_user else None

    feed_data = await activity_feed_service.get_feed_for_project(
        session, user_id=user_id, project_id=project_id, page=page, size=size
    )
    return feed_data


@router.get("/folder/{folder_id}", response_model=ActivityFeedResponse)
async def get_folder_feed(
    folder_id: UUID = Path(..., description="ID папки"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    size: int = Query(20, ge=1, le=100, description="Элементов на странице"),
    session: AsyncSession = Depends(get_async_session),
    current_user: Optional[User] = Depends(current_optional_user),
):
    """Получить ленту активности для конкретной папки."""
    user_id = current_user.id if current_user else None

    feed_data = await activity_feed_service.get_feed_for_folder(
        session, user_id=user_id, folder_id=folder_id, page=page, size=size
    )
    return feed_data


@router.get("/element/{element_id}", response_model=ActivityFeedResponse)
async def get_element_feed(
    element_id: UUID = Path(..., description="ID элемента"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    size: int = Query(20, ge=1, le=100, description="Элементов на странице"),
    session: AsyncSession = Depends(get_async_session),
    current_user: Optional[User] = Depends(current_optional_user),
):
    """Получить ленту активности для конкретного элемента."""
    user_id = current_user.id if current_user else None

    feed_data = await activity_feed_service.get_feed_for_element(
        session, user_id=user_id, element_id=element_id, page=page, size=size
    )
    return feed_data


@router.get("/project/{project_id}/heatmap", response_model=ActivityHeatmapResponse)
async def get_project_heatmap(
    project_id: UUID = Path(..., description="ID проекта"),
    start_date: date = Query(..., description="Дата начала в формате YYYY-MM-DD"),
    end_date: date = Query(..., description="Дата окончания в формате YYYY-MM-DD"),
    user_id_filter: Optional[UUID] = Query(
        None, description="Отфильтровать по ID пользователя"
    ),
    session: AsyncSession = Depends(get_async_session),
    current_user: Optional[User] = Depends(current_optional_user),
):
    """Получить данные об активности для heatmap-календаря."""
    if (end_date - start_date).days > 366:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date range cannot exceed 366 days.",
        )

    user_id = current_user.id if current_user else None

    heatmap_data = await activity_heatmap_service.get_heatmap_for_project(
        session,
        user_id=user_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        filter_user_id=user_id_filter,
    )
    return heatmap_data
