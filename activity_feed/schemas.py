from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date

class ActivityUserSchema(BaseModel):
    """Информация о пользователе, совершившем действие."""
    id: UUID
    name: Optional[str] = "Unknown User"

    class Config:
        from_attributes = True

class ActivityItemSchema(BaseModel):
    """Схема для одного агрегированного события в ленте."""
    id: int
    title: str = Field(..., description="Сгенерированный заголовок события")
    summary: Dict[str, Any] = Field(..., description="Структурированные детали события для рендеринга на фронте")
    started_at: datetime
    ended_at: datetime
    user: ActivityUserSchema

    class Config:
        from_attributes = True

class ActivityFeedResponse(BaseModel):
    """Схема для ответа API ленты активности с пагинацией."""
    items: List[ActivityItemSchema]
    total: int
    page: int
    size: int
    pages: int

class ActivityHeatmapItem(BaseModel):
    """Данные об активности за один день."""
    date: date
    count: int

class ActivityHeatmapResponse(BaseModel):
    """Ответ API для heatmap-календаря."""
    items: List[ActivityHeatmapItem]