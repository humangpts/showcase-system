import functools
from typing import Optional, Callable
from app.core.queue.connection import get_redis_client


def deduplicated(key: str, ttl: int = 60, prefix: str = "monitoring:dedup") -> Callable:
    """
    Decorator to ensure function runs only once within TTL window.
    Perfect for multi-worker environments.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                redis = await get_redis_client()
                dedup_key = f"{prefix}:{key}"

                # Atomic check-and-set
                is_first = await redis.set(dedup_key, "1", ex=ttl, nx=True)

                if not is_first:
                    return None  # Already executed

            except Exception:
                # If Redis fails, execute anyway
                pass

            return await func(*args, **kwargs)

        return wrapper

    return decorator
