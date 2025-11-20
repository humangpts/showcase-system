import pytest
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine
from sqlalchemy import text
from app.filemanager.core.unit_of_work import UnitOfWorkFactory

# Импортируем ваше исключение
from app.filemanager.core.exceptions import FolderNotFound


@pytest.mark.asyncio
class TestUnitOfWork:

    async def test_commit_persists_changes(self, db_session: AsyncSession, project):
        """Тест: commit должен сохранять изменения в БД."""
        async with UnitOfWorkFactory.create(db_session) as uow:
            folder = await uow.folders.create_folder(
                project_id=project.id,
                name="Test Folder",
                slug="test-folder",
                created_by=project.created_by,
            )
            folder_id = folder.id
            await uow.commit()

        # Проверяем в новой транзакции
        async with UnitOfWorkFactory.create(db_session) as uow:
            saved_folder = await uow.folders.get_folder_by_id(folder_id)
            assert saved_folder is not None
            assert saved_folder.name == "Test Folder"

    async def test_rollback_discards_changes(self, db_session: AsyncSession, project):
        """Тест: rollback должен отменять изменения."""
        folder_id = None

        try:
            async with UnitOfWorkFactory.create(db_session) as uow:
                folder = await uow.folders.create_folder(
                    project_id=project.id,
                    name="Test Folder 2",
                    slug="test-folder-2",
                    created_by=project.created_by,
                )
                folder_id = folder.id
                # Искусственно вызываем ошибку
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Проверяем, что папки НЕТ
        async with UnitOfWorkFactory.create(db_session) as uow:
            # ИСПРАВЛЕНИЕ: Ожидаем исключение, а не None
            try:
                await uow.folders.get_folder_by_id(folder_id)
                pytest.fail("Folder should not exist after rollback")
            except FolderNotFound:
                pass  # Это правильное поведение, папка не найдена

    async def test_auto_rollback_on_uncommitted_dirty_session(
        self, db_session: AsyncSession, project, test_engine: AsyncEngine
    ):
        """
        UnitOfWork не должен сохранять данные глобально, если не был вызван commit().
        """
        # 1. Пытаемся создать папку без commit()
        async with UnitOfWorkFactory.create(db_session) as uow:
            await uow.folders.create_folder(
                project_id=project.id,
                name="Uncommitted Folder",
                slug="uncommitted",
                created_by=project.created_by,
            )

        # 2. Проверяем через отдельное соединение, что записи НЕТ
        from sqlalchemy import select, func
        from app.filemanager.models.folder import FolderBase

        stmt = (
            select(func.count())
            .select_from(FolderBase)
            .where(FolderBase.slug == "uncommitted")
        )

        async with test_engine.connect() as conn:
            result = await conn.execute(stmt)
            count = result.scalar()
            assert count == 0

    async def test_clean_session_no_warning(self, db_session: AsyncSession, project):
        """Тест: только чтение не вызывает ошибок."""
        async with UnitOfWorkFactory.create(db_session) as uow:
            await uow.folders.get_folders(
                project_id=project.id, parent_id=None, page=1, size=10
            )
            assert not uow.is_dirty

    async def test_double_commit_is_safe(self, db_session: AsyncSession, project):
        """Тест: повторный commit безопасен."""
        async with UnitOfWorkFactory.create(db_session) as uow:
            await uow.folders.create_folder(
                project_id=project.id,
                name="Test Double",
                slug="test-double",
                created_by=project.created_by,
            )
            await uow.commit()
            await uow.commit()
            assert uow._committed
