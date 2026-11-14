"""顾虑整理：原文永远保留；摘要只在 LLM 可用时生成，并必须标注为辅助摘要。

摘要不得补造事实、身份或硬约束（技术设计 §12.3）；生成失败返回 None，
顾虑按无摘要提交，不影响提交流程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.llm.maas_client import LlmUnavailable, TextLlmClient

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 2000
_MAX_SUMMARY_CHARS = 200

_SYSTEM_PROMPT = (
    "你是顾虑摘要助手。把提交者的顾虑压缩成不超过 80 字的一段话，"
    "只保留原意，不得补造任何事实、身份或新的硬约束，不得推测提出者是谁。"
    "直接输出摘要，不要解释。"
)


def assist_summary(text: str, client: TextLlmClient) -> str | None:
    """返回辅助摘要；LLM 未启用、不可达或输出异常时返回 None（保持无摘要）。"""
    return summarize_concern(text, client).summary


@dataclass(frozen=True, slots=True)
class ConcernSummary:
    summary: str | None
    source: Literal["llm", "none"]
    degraded_reason: str | None = None


def summarize_concern(
    text: str, client: TextLlmClient | None
) -> ConcernSummary:
    """整理顾虑并显式标注来源；原文始终保留，失败不阻塞提交。"""
    if not text.strip():
        return ConcernSummary(None, "none", "empty_input")
    if client is None or not client.enabled:
        return ConcernSummary(None, "none", "llm_disabled")
    try:
        summary = client.chat(
            _SYSTEM_PROMPT, text[:_MAX_INPUT_CHARS], max_tokens=200
        )
    except LlmUnavailable:
        return ConcernSummary(None, "none", "llm_unavailable")
    summary = " ".join(summary.split())
    if not summary or len(summary) > _MAX_SUMMARY_CHARS:
        logger.info("concern summary rejected by length guard")
        return ConcernSummary(None, "none", "llm_bad_output")
    return ConcernSummary(summary, "llm")
