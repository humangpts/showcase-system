from uuid import UUID
from datetime import datetime, date
from typing import List

from sqlalchemy import String, ForeignKey, DateTime, Integer, Index, Date
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import PrimaryKeyConstraint
from sqlalchemy.sql import func

from app.core.datetime_utils import utc_now
from app.core.database import Base


class PendingActivity(Base):
    """Буфер для сырых событий перед агрегацией."""

    __tablename__ = "pending_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_pending_session_created", "session_key", "created_at"),
        Index("ix_pending_created", "created_at"),
        {"comment": "Buffer for raw activity events before aggregation"},
    )


class Activity(Base):
    """Основная таблица для фида с агрегированными событиями."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="aggregated_activity"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)

    affected_folders: Mapped[List[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default="{}",
        comment="UUIDs всех затронутых папок в этой активности",
    )
    affected_elements: Mapped[List[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default="{}",
        comment="UUIDs всех затронутых элементов в этой активности",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False, index=True
    )

    user = relationship("User", backref="activities")
    project = relationship("Project", backref="activities")

    __table_args__ = (
        Index("ix_activities_project_ended", "project_id", "ended_at"),
        Index("ix_activities_summary_gin", "summary", postgresql_using="gin"),
        Index(
            "ix_activities_affected_folders", "affected_folders", postgresql_using="gin"
        ),
        Index(
            "ix_activities_affected_elements",
            "affected_elements",
            postgresql_using="gin",
        ),
        {"comment": "Aggregated activity feed for projects"},
    )


class DailyActivitySummary(Base):
    """Сводная таблица для быстрой аналитики и heatmap."""

    __tablename__ = "daily_activity_summaries"

    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("activity_date", "project_id", "user_id"),
        {"comment": "Pre-aggregated daily activity counts for heatmaps and analytics"},
    )
