"""
Utility decorators for monitoring module.
"""

import functools
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def deduplicated(
    key: str, 
    ttl: int = 60,
    prefix: str = "monitoring:dedup"
) -> Callable:
    """
    Decorator to ensure function runs only once within TTL window.
    Perfect for multi-worker environments.
    
    Requires Redis adapter to be configured via set_redis_adapter().
    Falls back to always executing if Redis is not available.
    
    Args:
        key: Unique key for this operation
        ttl: Time-to-live in seconds
        prefix: Redis key prefix
        
    Example:
        >>> from monitoring import deduplicated, set_redis_adapter
        >>> 
        >>> @deduplicated(key="daily_report", ttl=3600)
        >>> async def send_daily_report():
        >>>     # This will run only once per hour across all workers
        >>>     ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Import here to avoid circular dependency
            from monitoring import get_redis_adapter
            
            redis_adapter = get_redis_adapter()
            
            if not redis_adapter:
                logger.debug(
                    f"No Redis adapter configured, executing {func.__name__} without deduplication"
                )
                return await func(*args, **kwargs)
            
            try:
                dedup_key = f"{prefix}:{key}"
                
                # Atomic check-and-set
                is_first = await redis_adapter.set(
                    dedup_key, "1", 
                    ex=ttl, 
                    nx=True
                )
                
                if not is_first:
                    logger.debug(f"Skipping {func.__name__}, already executed recently")
                    return None  # Already executed
                    
            except Exception as e:
                logger.warning(
                    f"Redis deduplication failed for {func.__name__}: {e}, "
                    "executing anyway"
                )
                # If Redis fails, execute anyway to ensure functionality
                pass
                
            return await func(*args, **kwargs)
        return wrapper
    return decorator