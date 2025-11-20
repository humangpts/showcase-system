"""
Unit of Work pattern implementation for FileManager.
Manages database transactions and repository access.
"""

import asyncio
from typing import Optional, TypeVar, Generic, Type, Dict, Any, Callable
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

from app.filemanager.logging import get_logger

logger = get_logger("app.filemanager.core.unit_of_work")

T = TypeVar("T")


class RepositoryProxy(Generic[T]):
    """Optimized proxy for repository to ensure session consistency."""

    def __init__(self, repository_class: Type[T], session: AsyncSession):
        self._repository_class = repository_class
        self._session = session
        self._instance: Optional[T] = None
        self._method_cache: Dict[str, Callable] = {}

    def _get_repository(self) -> T:
        """Lazy initialization of repository instance."""
        if self._instance is None:
            self._instance = self._repository_class()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        # Check cache first
        if name in self._method_cache:
            return self._method_cache[name]

        # Get repository instance
        repository = self._get_repository()

        # Get the method from repository
        method = getattr(repository, name)

        # If it's a callable, create wrapper and cache it
        if callable(method):

            async def wrapper(*args, **kwargs):
                result = method(self._session, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result

            # Cache the wrapper
            self._method_cache[name] = wrapper
            return wrapper

        return method


class UnitOfWork:
    """
    Unit of Work pattern implementation.
    Manages database transaction and provides access to repositories.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._committed = False
        self._rolled_back = False

        # Lazy import to avoid circular dependencies
        self._repository_classes = None

        # Initialize repository proxies
        self._init_repositories()

    def _init_repositories(self):
        """Initialize repository proxies with lazy import."""
        # Import repositories only when needed to avoid circular imports
        from app.filemanager.repositories import (
            ElementRepository,
            FolderRepository,
            TagRepository,
            PermissionRepository,
            TypeRepository,
            MentionRepository,
        )

        self.elements = RepositoryProxy(ElementRepository, self._session)
        self.folders = RepositoryProxy(FolderRepository, self._session)
        self.tags = RepositoryProxy(TagRepository, self._session)
        self.permissions = RepositoryProxy(PermissionRepository, self._session)
        self.types = RepositoryProxy(TypeRepository, self._session)
        self.mentions = RepositoryProxy(MentionRepository, self._session)

    @property
    def session(self) -> AsyncSession:
        """Get the underlying database session."""
        return self._session

    @property
    def is_dirty(self) -> bool:
        """
        Проверяет, есть ли в сессии несохраненные изменения.
        """
        return bool(self._session.new or self._session.dirty or self._session.deleted)

    async def commit(self) -> None:
        """Commit the transaction."""
        if self._committed:
            logger.warning("Transaction already committed")
            return

        if self._rolled_back:
            raise RuntimeError("Cannot commit after rollback")

        try:
            await self._session.commit()
            self._committed = True
            logger.debug("Transaction committed successfully")
        except Exception as e:
            logger.error(f"Error committing transaction: {str(e)}")
            await self.rollback()
            raise

    async def rollback(self) -> None:
        """Rollback the transaction."""
        if self._rolled_back:
            logger.warning("Transaction already rolled back")
            return

        try:
            await self._session.rollback()
            self._rolled_back = True
            logger.debug("Transaction rolled back")
        except Exception as e:
            logger.error(f"Error rolling back transaction: {str(e)}")
            # Don't re-raise rollback errors, just log them
            self._rolled_back = True

    async def flush(self) -> None:
        """Flush pending changes without committing."""
        await self._session.flush()

    async def refresh(self, instance) -> None:
        """Refresh an instance from the database."""
        await self._session.refresh(instance)

    def add(self, instance) -> None:
        """Add an instance to the session."""
        self._session.add(instance)

    async def close(self) -> None:
        """Close the session."""
        await self._session.close()

    @asynccontextmanager
    async def savepoint(self):
        """Create a savepoint within the transaction."""
        nested_transaction = await self._session.begin_nested()
        try:
            yield nested_transaction
        except Exception:
            await nested_transaction.rollback()
            raise
        else:
            await nested_transaction.commit()


class UnitOfWorkFactory:
    """Factory for creating UnitOfWork instances."""

    @staticmethod
    @asynccontextmanager
    async def create(session: AsyncSession):
        """
        Create a UnitOfWork instance as a context manager.

        Usage:
            async with UnitOfWorkFactory.create(session) as uow:
                # Do work with uow.elements, uow.folders, etc.
                await uow.commit()
        """
        uow = UnitOfWork(session)
        try:
            yield uow
        except Exception as e:
            logger.error(f"Error in UnitOfWork: {str(e)}")
            await uow.rollback()
            raise
        finally:
            # Если не было явного commit или rollback...
            if not uow._committed and not uow._rolled_back:
                # ...и если в сессии были реальные изменения...
                if uow.is_dirty:
                    # ...тогда выдаем предупреждение и откатываем транзакцию.
                    logger.warning(
                        "UnitOfWork exiting with pending changes and without explicit commit/rollback, rolling back"
                    )
                    await uow.rollback()
                # Если сессия "чистая" (только SELECT), то ничего не делаем.
                # Предупреждение не нужно, rollback бессмысленен.
