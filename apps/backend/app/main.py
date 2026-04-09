"""
FastAPI应用入口
"""
import sys
from pathlib import Path


from contextlib import asynccontextmanager
import subprocess
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.tasks import router as tasks_router
from app.api.system import router as system_router
from app.api.debug_vision import router as debug_vision_router
from app.core.task_manager import init_task_system, shutdown_task_system
from app.db.database import init_db, close_db
from app.utils.logger import logger
from app.utils.config import settings


def _get_en0_ip() -> str | None:
    """
    取得 macOS Wi‑Fi 介面 en0 的 IPv4（供真機測試填寫 baseURL）。
    失敗時回傳 None（不可影響服務啟動）。
    """
    try:
        if sys.platform != "darwin":
            return None
        p = subprocess.run(
            ["ipconfig", "getifaddr", "en0"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
        ip = (p.stdout or "").strip()
        return ip or None
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("Application starting up...")
    en0_ip = _get_en0_ip()
    if en0_ip:
        logger.info(f"en0 IPv4: {en0_ip}（iOS 真機 baseURL 可填 http://{en0_ip}:8000）")
    else:
        logger.info("en0 IPv4: --（非 macOS 或無法取得；真機請用 `ipconfig getifaddr en0` 查詢）")

    # 1. 初始化数据库
    await init_db()

    # 2. 初始化任务系统
    await init_task_system()

    logger.info("Application startup complete")

    yield

    # 关闭时
    logger.info("Application shutting down...")

    # 1. 关闭任务系统
    await shutdown_task_system()

    # 2. 关闭数据库连接
    await close_db()

    logger.info("Application shutdown complete")


# 创建FastAPI应用
app = FastAPI(
    title=f"{settings.APP_NAME}",
    description="Async task execution system",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")
app.include_router(debug_vision_router, prefix="/api/v1")

_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(_static_dir)),
        name="static",
    )


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
