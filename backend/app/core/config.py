from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parents[3] / ".env",
                  Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "privnegotiate"
    app_version: str = "0.1.0"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = (
        "mysql+pymysql://privnegotiate:change-me@127.0.0.1:3306/privnegotiate?charset=utf8mb4"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"

    db_pool_pre_ping: bool = True
    db_pool_recycle_seconds: int = 1800
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0)
    db_connect_timeout_seconds: int = Field(default=5, ge=1)
    redis_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    health_timeout_seconds: float = 0.5

    redis_ttl_seconds: int = 24 * 3600

    invite_token_secret: str = "dev-only-invite-secret"
    answer_deadline_seconds: int = 600
    solver_time_limit_seconds: float = 10.0
    max_members: int = 6

    # 菜单截图识别（华为云 MaaS，OpenAI 兼容接口）
    maas_base_url: str = "https://api.modelarts-maas.com/v1"
    maas_api_key: str = ""
    maas_vision_model: str = "qwen2.5-vl-72b-instruct"
    maas_timeout_seconds: float = 60.0
    menu_image_max_bytes: int = 6 * 1024 * 1024
    menu_ocr_access_token: str = ""
    menu_ocr_requests_per_minute: int = Field(default=5, ge=1)
    menu_ocr_global_requests_per_minute: int = Field(default=20, ge=1)
    menu_ocr_timeout_seconds: float = Field(default=10.0, gt=0)
    menu_ocr_max_concurrent: int = Field(default=4, ge=1, le=16)

    # 文本 LLM 三入口（底线解析/问题措辞/顾虑整理）。
    # 关闭或未配置时走固定模板与结构化表单，核心协商不依赖 LLM（技术设计 §12）。
    llm_text_enabled: bool = False
    maas_text_model: str = "qwen2.5-72b-instruct"
    llm_max_retries: int = 2
    llm_failure_threshold: int = 3
    llm_circuit_cooldown_seconds: float = 60.0

    # outbox 投影 worker：RDS 先落事实，worker 按 state_version 幂等投影 Redis。
    outbox_batch_size: int = 100
    outbox_max_attempts: int = 8
    worker_loop_interval_seconds: float = 1.0
    outbox_retry_base_seconds: float = Field(default=5.0, gt=0)
    outbox_retry_max_seconds: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def validate_production_secret(self) -> "Settings":
        if self.app_env == "prod" and (
            self.invite_token_secret == "dev-only-invite-secret"
            or len(self.invite_token_secret.strip()) < 32
        ):
            raise ValueError("prod requires a unique INVITE_TOKEN_SECRET of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
