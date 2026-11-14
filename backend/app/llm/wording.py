"""问题措辞：固定模板是主路径；LLM 只做语言润色，失败立即回退模板。

输入只包含程序冻结的公开候选名称，不含个人答案、理由、效用或他人底线；
无论是否润色，输出都不得改变候选集合、数量与顺序。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.llm.maas_client import LlmUnavailable, TextLlmClient

logger = logging.getLogger(__name__)

QUESTION_TEMPLATE = "请针对以下候选作答：{items}。勾选你能接受的项，并选出当页最想要的一个。"
SKIP_HINT = "如无法确定，可选择「暂不确定/跳过」。"

_POLISH_SYSTEM_PROMPT = (
    "你是协商应用的题目措辞助手。只把给定题目改写得更自然，"
    "不得增删或改变任何候选名称，不得改变候选顺序或数量，"
    "不得替用户预测偏好，不得添加承诺、诱导或评价。"
    "输出改写后的单句题目，不要任何解释。"
)
_MAX_POLISHED_LENGTH = 300


def render_question_text(item_labels: list[str] | tuple[str, ...]) -> str:
    """确定性模板：候选标签由服务端按展示顺序冻结传入。"""
    if not item_labels:
        raise ValueError("题目至少需要一个候选")
    return QUESTION_TEMPLATE.format(items="、".join(item_labels)) + SKIP_HINT


@dataclass(frozen=True, slots=True)
class QuestionWording:
    text: str
    source: Literal["llm", "template"]
    degraded_reason: str | None = None

    @property
    def degraded(self) -> bool:
        return self.degraded_reason is not None


def render_question_with_llm(
    item_labels: list[str] | tuple[str, ...],
    client: TextLlmClient | None = None,
) -> QuestionWording:
    """返回题目措辞与来源；不可用时回退模板并给出稳定降级原因。"""
    base = render_question_text(item_labels)
    if client is None or not client.enabled:
        return QuestionWording(base, "template", "llm_disabled")
    try:
        polished = client.chat(_POLISH_SYSTEM_PROMPT, base, max_tokens=200)
    except LlmUnavailable:
        return QuestionWording(base, "template", "llm_unavailable")
    if len(polished) > _MAX_POLISHED_LENGTH or "\n" in polished:
        return QuestionWording(base, "template", "llm_bad_output")
    if not all(label in polished for label in item_labels):
        logger.info("polished wording dropped candidates, falling back to template")
        return QuestionWording(base, "template", "llm_bad_output")
    return QuestionWording(polished, "llm")


def polish_question(item_labels: list[str] | tuple[str, ...], client: TextLlmClient) -> str:
    """LLM 润色失败、未启用或输出破坏约束时，一律回退固定模板，不阻塞派题。"""
    return render_question_with_llm(item_labels, client).text
