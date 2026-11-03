"""LLM 三入口降级路径：未启用/故障时必须回退模板或结构化表单。"""

from __future__ import annotations

import httpx
import pytest

from app.llm import concerns, parse_constraints, wording
from app.llm.maas_client import LlmUnavailable, TextLlmClient, default_config


class FakeLlm(TextLlmClient):
    """固定回复的假客户端；enabled=False 时按契约抛 LlmUnavailable。"""

    def __init__(self, replies: list[str] | str, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.replies = [replies] if isinstance(replies, str) else list(replies)

    def chat(self, system: str, user: str, *, max_tokens: int = 512) -> str:
        if not self.enabled:
            raise LlmUnavailable("LLM_DISABLED")
        if not self.replies:
            raise LlmUnavailable("LLM_EMPTY_OUTPUT")
        return self.replies.pop(0)


# ---------- maas_client ----------


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_client_disabled_raises_without_http():
    client = TextLlmClient(enabled=False)
    with pytest.raises(LlmUnavailable):
        client.chat("system", "user")


def test_client_success_parses_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "你好"}}],
                "usage": {"total_tokens": 7},
            },
        )

    client = TextLlmClient(enabled=True, http=_transport(handler))
    assert client.chat("s", "u") == "你好"


def test_client_retries_on_5xx_then_opens_circuit():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={})

    config = default_config()
    client = TextLlmClient(
        config.__class__(
            base_url=config.base_url,
            api_key="k",
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=1,
            failure_threshold=2,
            cooldown_seconds=60.0,
        ),
        enabled=True,
        http=_transport(handler),
    )
    with pytest.raises(LlmUnavailable):
        client.chat("s", "u")
    assert calls["n"] == 2  # 首次 + 1 次重试
    with pytest.raises(LlmUnavailable):
        client.chat("s", "u")
    assert calls["n"] == 4
    # 连续失败达到阈值，熔断后不再发起 HTTP 请求
    with pytest.raises(LlmUnavailable):
        client.chat("s", "u")
    assert calls["n"] == 4


def test_client_4xx_does_not_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={})

    client = TextLlmClient(enabled=True, http=_transport(handler))
    with pytest.raises(LlmUnavailable):
        client.chat("s", "u")
    assert calls["n"] == 1


# ---------- wording ----------


def test_template_contains_labels_and_skip_hint():
    text = wording.render_question_text(("宫保鸡丁", "清蒸鱼"))
    assert "宫保鸡丁" in text and "清蒸鱼" in text
    assert wording.SKIP_HINT in text


def test_template_rejects_empty_items():
    with pytest.raises(ValueError):
        wording.render_question_text(())


def test_polish_falls_back_when_disabled():
    client = FakeLlm("", enabled=False)
    assert wording.polish_question(("A", "B"), client) == wording.render_question_text(("A", "B"))


def test_polish_accepts_constrained_rewrite():
    polished = "请看这三道菜：宫保鸡丁、清蒸鱼、麻婆豆腐，勾选可接受的并选出最想要的。如无法确定，可选择「暂不确定/跳过」。"
    client = FakeLlm(polished)
    labels = ("宫保鸡丁", "清蒸鱼", "麻婆豆腐")
    assert wording.polish_question(labels, client) == polished


def test_polish_rejects_rewrite_dropping_candidates():
    client = FakeLlm("今天只有宫保鸡丁可选哦。")
    labels = ("宫保鸡丁", "清蒸鱼")
    assert wording.polish_question(labels, client) == wording.render_question_text(labels)


def test_polish_rejects_overlong_or_multiline():
    labels = ("A", "B")
    client = FakeLlm(["好\n坏"])  # 多行
    assert wording.polish_question(labels, client) == wording.render_question_text(labels)
    client = FakeLlm(["好" * 301])
    assert wording.polish_question(labels, client) == wording.render_question_text(labels)


# ---------- parse_constraints ----------


def test_parse_empty_text_needs_form():
    result = parse_constraints.parse_bottomline_text("  ", ["c01"])
    assert result.needs_form and result.reason == "empty_input"


def test_parse_disabled_client_needs_form():
    result = parse_constraints.parse_bottomline_text(
        "我不吃辣", ["c01"], FakeLlm("", enabled=False)
    )
    assert result.needs_form and result.reason == "llm_disabled"


def test_parse_llm_failure_needs_form():
    class Boom(TextLlmClient):
        def __init__(self) -> None:
            super().__init__(enabled=True)

        def chat(self, system: str, user: str, *, max_tokens: int = 512) -> str:
            raise LlmUnavailable("LLM_UNREACHABLE")

    result = parse_constraints.parse_bottomline_text("我不吃辣", ["c01"], Boom())
    assert result.needs_form and result.reason == "llm_unavailable"


def test_parse_bad_json_needs_form():
    result = parse_constraints.parse_bottomline_text(
        "我不吃辣", ["c01"], FakeLlm("我觉得不行")
    )
    assert result.needs_form and result.reason == "llm_bad_output"


def test_parse_filters_unknown_candidates_and_invalid_rows():
    reply = (
        '{"proposals": ['
        '{"kind": "exclude_item", "candidate_ids": ["c01", "c99"]},'
        '{"kind": "opt_out", "candidate_ids": []},'
        '{"kind": "require_count", "category": "荤菜", "min_count": 1}'
        "]}"
    )
    result = parse_constraints.parse_bottomline_text(
        "别选 c01，荤菜至少一道", ["c01", "c02"], FakeLlm(reply)
    )
    assert not result.needs_form
    assert [p.kind for p in result.proposals] == ["exclude_item", "require_count"]
    assert result.proposals[0].candidate_ids == ("c01",)


def test_parse_wraps_code_fence_output():
    reply = '```json\n{"proposals": [{"kind": "exclude_item", "candidate_ids": ["c02"]}]}\n```'
    result = parse_constraints.parse_bottomline_text("c02 别选", ["c02"], FakeLlm(reply))
    assert not result.needs_form and result.proposals[0].kind == "exclude_item"


# ---------- concerns ----------


def test_concern_summary_none_when_disabled():
    assert concerns.assist_summary("别选太辣的", FakeLlm("摘要", enabled=False)) is None


def test_concern_summary_generated_when_enabled():
    assert concerns.assist_summary("别选太辣的", FakeLlm(" 希望避免辣菜。 ")) == "希望避免辣菜。"


def test_concern_summary_rejects_overlong():
    assert concerns.assist_summary("顾虑", FakeLlm("长" * 201)) is None


def test_concern_summary_none_on_failure():
    class Boom(TextLlmClient):
        def __init__(self) -> None:
            super().__init__(enabled=True)

        def chat(self, system: str, user: str, *, max_tokens: int = 512) -> str:
            raise LlmUnavailable("LLM_CIRCUIT_OPEN")

    assert concerns.assist_summary("顾虑", Boom()) is None
