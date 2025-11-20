"""
Common utilities for monitoring module.
"""

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """
    Returns current UTC time as naive datetime.
    Compatible with PostgreSQL TIMESTAMP WITHOUT TIME ZONE.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware_utc_now() -> datetime:
    """
    Returns current UTC time as aware datetime.
    Compatible with PostgreSQL TIMESTAMP WITH TIME ZONE.
    """
    return datetime.now(timezone.utc)


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Convert datetime with timezone to naive datetime in UTC.
    
    Args:
        dt: DateTime object with or without timezone
        
    Returns:
        Naive datetime in UTC or None
    """
    if dt is None:
        return None
    
    if dt.tzinfo is not None:
        # Convert to UTC and remove timezone
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        # Already naive, return as is
        return dt