from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
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
    health_timeout_seconds: float = 0.5

    redis_ttl_seconds: int = 24 * 3600

    invite_token_secret: str = "dev-only-invite-secret"
    answer_deadline_seconds: int = 600
    solver_time_limit_seconds: float = 10.0
    max_members: int = 15
    min_members: int = 3

    # 菜单截图识别（华为云 MaaS，OpenAI 兼容接口）
    maas_base_url: str = "https://api.modelarts-maas.com/v1"
    maas_api_key: str = ""
    maas_vision_model: str = "qwen2.5-vl-72b-instruct"
    maas_timeout_seconds: float = 60.0
    menu_image_max_bytes: int = 6 * 1024 * 1024

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
