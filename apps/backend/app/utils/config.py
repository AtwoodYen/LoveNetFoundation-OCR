"""
应用配置管理
"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings
from typing import Literal, Optional
from pathlib import Path


def _default_assets_dir() -> str:
    """apps/assets（與 backend 同屬 apps 下）"""
    return str((Path(__file__).resolve().parents[3] / "assets").resolve())


class Settings(BaseSettings):
    """应用配置"""

    # 基础配置
    APP_NAME: str = "OCR Task System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置
    DATABASE_URL: str = "sqlite+aiosqlite:///./tasks.db"

    # 输出目录
    OUTPUT_DIR: str = "./data"

    # 靜態資源（表單範本 JSON、參考 PDF 等）
    ASSETS_DIR: str = _default_assets_dir()

    # 版面 / OCR 推論服務（與本 API 分離；Worker 會對此 URL 送 POST）
    # .env 可用 LAYOUT_OCR_URL 或 layout_ocr_url（case_sensitive=True 時需別名）
    layout_ocr_url: str = Field(
        default="http://127.0.0.1:5002/glmocr/parse",
        validation_alias=AliasChoices("LAYOUT_OCR_URL", "layout_ocr_url"),
    )

    # Google Cloud Vision API Key
    # 環境變數：GOOGLE_VISION_API_KEY
    GOOGLE_VISION_API_KEY: str = ""

    # Worker配置
    RUN_WORKERS: bool = True
    WORKER_COUNT: int = 5
    WORKER_POLL_INTERVAL: int = 5  # 秒
    TASK_TIMEOUT: int = 3600  # 秒（1小时）

    # 任务配置
    MAX_QUEUE_SIZE: int = 100
    MAX_CONCURRENT_TASKS: int = 5
    DEFAULT_MAX_RETRIES: int = 3
    DEFAULT_RETRY_DELAY: int = 60  # 秒

    # 清理配置
    CLEANUP_INTERVAL: int = 300  # 秒（5分钟）
    OLD_TASK_DAYS: int = 30  # 天

    # 恢复配置
    RECOVERY_INTERVAL: int = 3600  # 秒（1小时）

    # 监控配置
    METRICS_ENABLED: bool = True
    METRICS_INTERVAL: int = 60  # 秒

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    environment: Literal["development", "testing", "production"] = "development"

    class Config:
        env_file = ".env"
        case_sensitive = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 确保输出目录存在
        Path(self.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


# 创建全局配置实例
settings = Settings()
