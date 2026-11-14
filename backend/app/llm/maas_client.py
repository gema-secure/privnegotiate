"""MaaS 文本 LLM 客户端：OpenAI 兼容接口，带超时、重试与熔断。

边界（技术设计 §12）：
- llm_text_enabled 且配置了 api_key 才启用；未启用时调用方必须走
  固定模板 / 结构化表单降级，核心协商不得依赖本模块。
- 日志只记录状态与用量，不留私人原文。
- 现实状态：项目尚未取得华为云 MaaS 文本调用凭证，默认关闭。本文档只描述
  可配置的调用约定，不代表已在真实 MaaS 端点验证通过。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LlmUnavailable(RuntimeError):
    """LLM 未配置、不可达或熔断中；调用方收到后必须降级，不得重试阻塞流程。"""


@dataclass(frozen=True, slots=True)
class TextLlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_retries: int
    failure_threshold: int
    cooldown_seconds: float


def default_config() -> TextLlmConfig:
    return TextLlmConfig(
        base_url=settings.maas_base_url,
        api_key=settings.maas_api_key,
        model=settings.maas_text_model,
        timeout_seconds=min(settings.maas_timeout_seconds, 20.0),
        max_retries=settings.llm_max_retries,
        failure_threshold=settings.llm_failure_threshold,
        cooldown_seconds=settings.llm_circuit_cooldown_seconds,
    )


class TextLlmClient:
    """文本对话客户端；三入口共享一个实例以共享熔断状态。"""

    def __init__(
        self,
        config: TextLlmConfig | None = None,
        *,
        enabled: bool | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._config = config or default_config()
        self._enabled = (
            (settings.llm_text_enabled and bool(self._config.api_key))
            if enabled is None
            else enabled
        )
        self._http = http
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def model(self) -> str:
        return self._config.model

    def chat(self, system: str, user: str, *, max_tokens: int = 512) -> str:
        """返回补全文本；任何失败都抛 LlmUnavailable，由调用方降级。"""
        if not self._enabled:
            raise LlmUnavailable("LLM_DISABLED")
        with self._lock:
            if time.monotonic() < self._open_until:
                raise LlmUnavailable("LLM_CIRCUIT_OPEN")
        last_error: LlmUnavailable | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                text = self._once(system, user, max_tokens)
            except LlmUnavailable as exc:
                last_error = exc
                if not exc.args or exc.args[0] not in ("LLM_TIMEOUT", "LLM_UNREACHABLE", "LLM_SERVER_ERROR"):
                    break  # 配置/输出类失败重试无意义
                continue
            with self._lock:
                self._consecutive_failures = 0
            return text
        self._record_failure()
        assert last_error is not None
        raise last_error

    def _once(self, system: str, user: str, max_tokens: int) -> str:
        body = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        url = f"{self._config.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        try:
            if self._http is not None:
                response = self._http.post(
                    url, json=body, headers=headers, timeout=self._config.timeout_seconds
                )
            else:
                response = httpx.post(
                    url, json=body, headers=headers, timeout=self._config.timeout_seconds
                )
        except httpx.HTTPError:
            raise LlmUnavailable("LLM_UNREACHABLE") from None
        if response.status_code >= 500:
            raise LlmUnavailable("LLM_SERVER_ERROR")
        if response.status_code >= 400:
            logger.warning("llm text request rejected status=%s", response.status_code)
            raise LlmUnavailable("LLM_REQUEST_REJECTED")
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError):
            raise LlmUnavailable("LLM_BAD_RESPONSE") from None
        logger.info(
            "llm text ok model=%s total_tokens=%s",
            self._config.model,
            usage.get("total_tokens"),
        )
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = str(content).strip()
        if not text:
            raise LlmUnavailable("LLM_EMPTY_OUTPUT")
        return text

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._config.failure_threshold:
                self._open_until = time.monotonic() + self._config.cooldown_seconds
                logger.warning(
                    "llm circuit open for %ss after %s failures",
                    self._config.cooldown_seconds,
                    self._consecutive_failures,
                )


_CLIENT: TextLlmClient | None = None


def get_text_llm_client() -> TextLlmClient:
    """三入口共享同一个客户端，共享熔断状态；测试可用依赖覆盖替换。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = TextLlmClient()
    return _CLIENT
