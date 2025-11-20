"""
Adapters for integration with different frameworks and databases.
Users can implement their own adapters for custom integration.
"""

from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime


class DatabaseAdapter(ABC):
    """
    Adapter for database operations needed by monitoring.
    Implement this to integrate with your database.
    """
    
    @abstractmethod
    async def get_new_users_count(
        self, 
        start_date: datetime, 
        end_date: datetime
    ) -> int:
        """
        Count new users created between start_date and end_date.
        
        Args:
            start_date: Start of period
            end_date: End of period
            
        Returns:
            Number of new users
        """
        pass
    
    @abstractmethod
    async def get_active_users_count(
        self, 
        start_date: datetime, 
        end_date: datetime
    ) -> int:
        """
        Count active users in the period.
        
        Args:
            start_date: Start of period
            end_date: End of period
            
        Returns:
            Number of active users
        """
        pass
    
    @abstractmethod
    async def get_total_users_count(self) -> int:
        """
        Get total number of users.
        
        Returns:
            Total user count
        """
        pass
    
    @abstractmethod
    async def get_new_projects_count(
        self, 
        start_date: datetime, 
        end_date: datetime
    ) -> int:
        """Count new projects in period."""
        pass
    
    @abstractmethod
    async def get_updated_projects_count(
        self, 
        start_date: datetime, 
        end_date: datetime
    ) -> int:
        """Count updated projects in period."""
        pass
    
    @abstractmethod
    async def get_total_projects_count(self) -> int:
        """Get total projects count."""
        pass
    
    @abstractmethod
    async def health_check(self, timeout: float = 5.0) -> bool:
        """
        Check if database is healthy.
        
        Args:
            timeout: Query timeout in seconds
            
        Returns:
            True if healthy, False otherwise
        """
        pass


class QueueAdapter(ABC):
    """
    Adapter for background queue operations.
    Implement this to integrate with your queue system (ARQ, Celery, etc).
    """
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if queue is healthy and processing jobs.
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    @abstractmethod
    async def get_queue_size(self) -> int:
        """
        Get number of pending jobs in queue.
        
        Returns:
            Queue size
        """
        pass
    
    @abstractmethod
    async def get_last_job_time(self) -> Optional[float]:
        """
        Get timestamp of last completed job.
        
        Returns:
            Unix timestamp or None
        """
        pass


class RedisAdapter(ABC):
    """
    Adapter for Redis operations.
    The monitoring module uses Redis for caching and deduplication.
    """
    
    @abstractmethod
    async def get(self, key: str) -> Optional[str]:
        """Get value from Redis."""
        pass
    
    @abstractmethod
    async def set(
        self, 
        key: str, 
        value: str, 
        ex: Optional[int] = None,
        nx: bool = False,
        xx: bool = False
    ) -> bool:
        """Set value in Redis with optional TTL."""
        pass
    
    @abstractmethod
    async def setex(self, key: str, seconds: int, value: str) -> bool:
        """Set value with expiration."""
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> int:
        """Delete key from Redis."""
        pass
    
    @abstractmethod
    async def incr(self, key: str) -> int:
        """Increment counter."""
        pass
    
    @abstractmethod
    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration on key."""
        pass
    
    @abstractmethod
    async def lpush(self, key: str, *values: str) -> int:
        """Push to list."""
        pass
    
    @abstractmethod
    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        """Get range from list."""
        pass
    
    @abstractmethod
    async def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list."""
        pass
    
    @abstractmethod
    async def scan(
        self, 
        cursor: int = 0, 
        match: Optional[str] = None, 
        count: int = 100
    ) -> tuple:
        """Scan keys matching pattern."""
        pass
    
    @abstractmethod
    async def ping(self) -> bool:
        """Ping Redis."""
        pass
    
    @abstractmethod
    async def type(self, key: str) -> str:
        """Get key type."""
        pass
    
    @abstractmethod
    async def zcard(self, key: str) -> int:
        """Get sorted set size."""
        pass
    
    @abstractmethod
    async def llen(self, key: str) -> int:
        """Get list length."""
        pass


# Default implementations for common cases

class DefaultRedisAdapter(RedisAdapter):
    """
    Default Redis adapter using redis.asyncio.
    Works out of the box with standard Redis setup.
    """
    
    def __init__(self, redis_client):
        """
        Args:
            redis_client: redis.asyncio.Redis client instance
        """
        self._client = redis_client
    
    async def get(self, key: str) -> Optional[str]:
        value = await self._client.get(key)
        return value.decode('utf-8') if isinstance(value, bytes) else value
    
    async def set(
        self, 
        key: str, 
        value: str, 
        ex: Optional[int] = None,
        nx: bool = False,
        xx: bool = False
    ) -> bool:
        result = await self._client.set(key, value, ex=ex, nx=nx, xx=xx)
        return bool(result)
    
    async def setex(self, key: str, seconds: int, value: str) -> bool:
        result = await self._client.setex(key, seconds, value)
        return bool(result)
    
    async def delete(self, key: str) -> int:
        return await self._client.delete(key)
    
    async def incr(self, key: str) -> int:
        return await self._client.incr(key)
    
    async def expire(self, key: str, seconds: int) -> bool:
        return await self._client.expire(key, seconds)
    
    async def lpush(self, key: str, *values: str) -> int:
        return await self._client.lpush(key, *values)
    
    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        values = await self._client.lrange(key, start, end)
        return [v.decode('utf-8') if isinstance(v, bytes) else v for v in values]
    
    async def ltrim(self, key: str, start: int, end: int) -> bool:
        return await self._client.ltrim(key, start, end)
    
    async def scan(
        self, 
        cursor: int = 0, 
        match: Optional[str] = None, 
        count: int = 100
    ) -> tuple:
        cursor, keys = await self._client.scan(cursor, match=match, count=count)
        decoded_keys = [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
        return cursor, decoded_keys
    
    async def ping(self) -> bool:
        return await self._client.ping()
    
    async def type(self, key: str) -> str:
        key_type = await self._client.type(key)
        return key_type.decode('utf-8') if isinstance(key_type, bytes) else key_type
    
    async def zcard(self, key: str) -> int:
        return await self._client.zcard(key)
    
    async def llen(self, key: str) -> int:
        return await self._client.llen(key)


class NullDatabaseAdapter(DatabaseAdapter):
    """
    Null adapter that returns zeros.
    Used when database statistics are not needed.
    """
    
    async def get_new_users_count(self, start_date, end_date) -> int:
        return 0
    
    async def get_active_users_count(self, start_date, end_date) -> int:
        return 0
    
    async def get_total_users_count(self) -> int:
        return 0
    
    async def get_new_projects_count(self, start_date, end_date) -> int:
        return 0
    
    async def get_updated_projects_count(self, start_date, end_date) -> int:
        return 0
    
    async def get_total_projects_count(self) -> int:
        return 0
    
    async def health_check(self, timeout: float = 5.0) -> bool:
        return True


class NullQueueAdapter(QueueAdapter):
    """
    Null adapter that reports healthy.
    Used when queue monitoring is not needed.
    """
    
    async def health_check(self) -> bool:
        return True
    
    async def get_queue_size(self) -> int:
        return 0
    
    async def get_last_job_time(self) -> Optional[float]:
        return None