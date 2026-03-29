"""
任务Repository
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TaskPriority
from app.repository.base import BaseRepository


class TaskRepository(BaseRepository[Task]):
    """任务Repository - 支持分布式锁和重试"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Task)

    async def create_task(
        self,
        task_id: str,
        document_id: str,
        original_filename: str,
        file_type: str,
        file_size: int,
        file_path: str,
        processing_mode: str = "pipeline",
        priority: int = TaskPriority.NORMAL,
        ocr_config: Optional[Dict[str, Any]] = None,
        output_format: str = "markdown",
        retry_config: Optional[Dict[str, Any]] = None
    ) -> Task:
        """创建任务"""
        task_data = {
            "task_id": task_id,
            "document_id": document_id,
            "original_filename": original_filename,
            "file_type": file_type,
            "file_size": file_size,
            "file_path": file_path,
            "processing_mode": processing_mode,
            "priority": priority,
            "ocr_config": ocr_config or {},
            "output_format": output_format,
            "status": TaskStatus.PENDING,
            "progress": 0.0
        }

        # 添加重试配置
        if retry_config:
            task_data["max_retries"] = retry_config.get("max_retries", 3)
            task_data["retry_delay"] = retry_config.get("base_delay", 60)

        return await self.create(**task_data)

    async def get_by_task_id(self, task_id: str) -> Optional[Task]:
        """根据task_id获取任务"""
        stmt = select(Task).where(Task.task_id == task_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def acquire_task_lock(
        self,
        task_id: str,
        worker_id: str,
        lock_timeout: int = 3600
    ) -> Optional[Task]:
        """
        尝试获取任务锁（原子操作）

        Args:
            task_id: 任务ID
            worker_id: Worker ID
            lock_timeout: 锁超时时间（秒）

        Returns:
            Optional[Task]: 成功返回任务对象，失败返回None
        """
        lock_expires_at = datetime.now(UTC) + timedelta(seconds=lock_timeout)

        # 使用UPDATE ... WHERE语句原子性获取锁
        stmt = (
            update(Task)
            .where(
                and_(
                    Task.task_id == task_id,
                    or_(
                        Task.status == TaskStatus.PENDING,
                        # 锁已过期的PROCESSING任务也可以被重新获取
                        and_(
                            Task.status == TaskStatus.PROCESSING,
                            Task.lock_expires_at < datetime.now(UTC)
                        )
                    )
                )
            )
            .values(
                status=TaskStatus.PROCESSING,
                worker_id=worker_id,
                lock_expires_at=lock_expires_at,
                started_at=datetime.now(UTC)
            )
            .returning(Task)
        )

        result = await self.session.execute(stmt)
        task = result.scalar_one_or_none()

        if task:
            # 刷新任务对象
            await self.session.refresh(task)

        return task

    async def renew_task_lock(
        self,
        task_id: str,
        worker_id: str,
        lock_timeout: int = 3600
    ) -> bool:
        """
        续期任务锁

        Args:
            task_id: 任务ID
            worker_id: Worker ID
            lock_timeout: 新的锁超时时间（秒）

        Returns:
            bool: 是否成功续期
        """
        new_expires_at = datetime.now(UTC) + timedelta(seconds=lock_timeout)

        stmt = (
            update(Task)
            .where(
                and_(
                    Task.task_id == task_id,
                    Task.worker_id == worker_id,
                    Task.status == TaskStatus.PROCESSING
                )
            )
            .values(lock_expires_at=new_expires_at)
        )

        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def release_task_lock(
        self,
        task_id: str,
        worker_id: str,
        status: str = TaskStatus.COMPLETED,
        error_message: Optional[str] = None
    ) -> bool:
        """
        释放任务锁

        Args:
            task_id: 任务ID
            worker_id: Worker ID
            status: 最终状态
            error_message: 错误信息（如果有）

        Returns:
            bool: 是否成功释放
        """
        update_data = {
            "status": status,
            "completed_at": datetime.now(UTC),
            "worker_id": None,
            "lock_expires_at": None
        }

        if status == TaskStatus.COMPLETED:
            update_data["progress"] = 100.0

        if error_message:
            update_data["error_message"] = error_message

        stmt = (
            update(Task)
            .where(
                and_(
                    Task.task_id == task_id,
                    Task.worker_id == worker_id
                )
            )
            .values(**update_data)
        )

        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_expired_locks(self, limit: int = 100) -> List[Task]:
        """获取锁已过期的任务"""
        stmt = (
            select(Task)
            .where(
                and_(
                    Task.status == TaskStatus.PROCESSING,
                    Task.lock_expires_at < datetime.now(UTC)
                )
            )
            .order_by(Task.lock_expires_at)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def reset_task_for_retry(
        self,
        task_id: str,
        error_message: str
    ) -> Optional[Task]:
        """
        重置任务以便重试

        Args:
            task_id: 任务ID
            error_message: 错误信息

        Returns:
            Optional[Task]: 重置后的任务
        """
        task = await self.get_by_task_id(task_id)
        if not task or not task.can_retry:
            return None

        stmt = (
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status=TaskStatus.PENDING,
                worker_id=None,
                lock_expires_at=None,
                retry_count=task.retry_count + 1,
                last_retry_at=datetime.now(UTC),
                error_message=error_message,
                started_at=None,  # 重置开始时间
                current_step=None,
                progress=0.0  # 重置进度
            )
            .returning(Task)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_tasks(
        self,
        limit: int = 10
    ) -> List[Task]:
        """获取待处理的任务（按优先级排序）"""
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.PENDING)
            .order_by(Task.priority.desc(), Task.created_at)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_processing_tasks(self) -> List[Task]:
        """获取正在处理的任务"""
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.PROCESSING)
            .order_by(Task.started_at)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        progress: Optional[float] = None,
        current_step: Optional[str] = None,
        error_message: Optional[str] = None,
        result_file_path: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None
    ) -> Optional[Task]:
        """更新任务状态"""
        update_data = {"status": status}

        if progress is not None:
            update_data["progress"] = progress

        if current_step is not None:
            update_data["current_step"] = current_step

        if error_message is not None:
            update_data["error_message"] = error_message

        if result_file_path is not None:
            update_data["result_file_path"] = result_file_path

        if result is not None:
            from app.utils.json_safe import json_sanitize

            update_data["result"] = json_sanitize(result)

        # 设置时间戳
        if status == TaskStatus.PROCESSING:
            update_data["started_at"] = datetime.now(UTC)
        elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            update_data["completed_at"] = datetime.now(UTC)

        stmt = (
            update(Task)
            .where(Task.task_id == task_id)
            .values(**update_data)
            .returning(Task)
        )

        result = await self.session.execute(stmt)
        db_obj = result.scalar_one_or_none()

        if db_obj:
            await self.session.refresh(db_obj)

        return db_obj

    async def update_task_progress(
        self,
        task_id: str,
        progress: float,
        current_step: Optional[str] = None
    ) -> Optional[Task]:
        """更新任务进度"""
        update_data = {"progress": min(max(progress, 0.0), 100.0)}

        if current_step:
            update_data["current_step"] = current_step

        stmt = (
            update(Task)
            .where(Task.task_id == task_id)
            .values(**update_data)
            .returning(Task)
        )

        result = await self.session.execute(stmt)
        db_obj = result.scalar_one_or_none()

        if db_obj:
            await self.session.refresh(db_obj)

        return db_obj

    async def list_tasks_by_status(
        self,
        status: Optional[str] = None,
        processing_mode: Optional[str] = None,
        priority: Optional[int] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Task]:
        """根据条件查询任务列表"""
        stmt = select(Task)

        conditions = []
        if status:
            conditions.append(Task.status == status)
        if processing_mode:
            conditions.append(Task.processing_mode == processing_mode)
        if priority:
            conditions.append(Task.priority == priority)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(Task.created_at.desc()).offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_tasks_by_status(self) -> Dict[str, int]:
        """按状态统计任务数量"""
        stmt = (
            select(Task.status, func.count(Task.id))
            .group_by(Task.status)
        )

        result = await self.session.execute(stmt)
        return {status: count for status, count in result.all()}

    async def cleanup_old_tasks(
        self,
        days: int = 30,
        statuses: Optional[List[str]] = None
    ) -> int:
        """清理旧任务"""
        from sqlalchemy import delete

        if statuses is None:
            statuses = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]

        cutoff_date = datetime.now(UTC) - timedelta(days=days)

        stmt = (
            delete(Task)
            .where(
                and_(
                    Task.status.in_(statuses),
                    Task.completed_at < cutoff_date
                )
            )
        )

        result = await self.session.execute(stmt)
        return result.rowcount
