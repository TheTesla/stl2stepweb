import os
import yaml
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from functools import lru_cache


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class QueueConfig(BaseModel):
    max_parallel_conversions: int = 2
    max_queue_size: int = 100


class ConversionConfig(BaseModel):
    timeout_seconds: int = 300
    memory_limit_mb: int = 2048


class SessionConfig(BaseModel):
    max_file_retention_seconds: int = 3600
    keep_alive_interval_seconds: int = 30
    cleanup_interval_seconds: int = 60


class UploadConfig(BaseModel):
    max_file_size_mb: int = 100
    allowed_extensions: list[str] = [".stl", ".STL"]


class StorageConfig(BaseModel):
    temp_dir: str = "/tmp/stl2step_sessions"
    upload_dir: str = "/tmp/stl2step_uploads"


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    queue: QueueConfig = QueueConfig()
    conversion: ConversionConfig = ConversionConfig()
    session: SessionConfig = SessionConfig()
    upload: UploadConfig = UploadConfig()
    storage: StorageConfig = StorageConfig()


@lru_cache()
def get_settings() -> Settings:
    config_path = Path(os.getenv("STL2STEP_CONFIG", "config/settings.yaml"))
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        return Settings(**data)
    return Settings()


def ensure_dirs(settings: Settings):
    Path(settings.storage.temp_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.storage.upload_dir).mkdir(parents=True, exist_ok=True)