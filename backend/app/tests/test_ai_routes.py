"""三入口真实业务路径：解析只提议、措辞只表达、顾虑整理可修正，失败必降级。"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.llm.maas_client import LlmUnavailable, get_text_llm_client
from app.main import app
from app.models.business import ConfirmedConstraint
from app.tests.test_api_flow import _answer, _close, _candidates, _me, _start


class FakeClient:
    def __init__(self, output: str | None = None, *, fail: bool = False) -> None:
        self.enabled = True
        self.model = "fake-text-model"
        self.output = output
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, *, max_tokens: int = 512) -> str:
        self.calls.append((system, user))
        if self.fail:
            raise LlmUnavailable("LLM_TIMEOUT")
        if callable(self.output):
            return self.output(system, user)
        return self.output or ""


class DisabledClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = False


def _use(client) -> None:
    app.dependency_overrides[get_text_llm_client] = lambda: client


def _create_unconfirmed(client):
    created = client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "candidates": _candidates(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": 2,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    joined = client.post(
        f"/negotiations/{body['negotiation_id']}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    return body["negotiation_id"], {
        "Authorization": f"Bearer {body['member_token']}"
    }


def test_bottomline_parse_only_proposes_and_degrades_without_credentials(api) -> None:
    client, factory = api
    negotiation_id, headers = _create_unconfirmed(client)

    _use(DisabledClient())
    response = client.post(
        f"/negotiations/{negotiation_id}/me/bottomline/parse",
        json={"text": "我不想吃 c01，另外至少来一道 main"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["needs_form"] is True
    assert body["reason"] == "llm_disabled"
    assert body["degraded"] is True
    assert body["requires_confirmation"] is True
    assert body["proposals"] == []

    proposal_json = json.dumps(
        {
            "proposals": [
                {"kind": "opt_out", "candidate_ids": ["c01"]},
                {"kind": "require_count", "category": "main", "min_count": 1},
            ]
        }
    )
    _use(FakeClient(proposal_json))
    parsed = client.post(
        f"/negotiations/{negotiation_id}/me/bottomline/parse",
        json={"text": "我不想吃 c01，另外至少来一道 main"},
        headers=headers,
    )
    assert parsed.status_code == 200, parsed.text
    payload = parsed.json()
    assert payload["needs_form"] is False
    assert payload["model"] == "fake-text-model"
    assert payload["requires_confirmation"] is True
    assert [item["kind"] for item in payload["proposals"]] == [
        "opt_out",
        "require_count",
    ]

    session = factory()
    try:
        rows = session.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.owner_member_id.is_not(None)
            )
        ).all()
    finally:
        session.close()
    assert rows == []


def test_question_wording_never_changes_items_and_falls_back(api) -> None:
    client, _ = api
    _use(FakeClient(lambda system, user: user + "（已润色）"))
    negotiation_id, headers = _start(client, round_limit=1)
    batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert batch["wording_source"] == "llm"
    assert batch["wording_degraded"] is False
    assert "已润色" in batch["prompt_text"]
    for item in batch["items"]:
        assert item["name"] in batch["prompt_text"]

    forced = client.get(
        f"/negotiations/{negotiation_id}/me?wording=template", headers=headers["a"]
    )
    assert forced.status_code == 200
    forced_batch = forced.json()["question_batch"]
    assert forced_batch["wording_source"] == "template"
    assert forced_batch["wording_degraded"] is False
    assert forced_batch["prompt_text"].endswith("暂不确定/跳过」。")
    assert [item["candidate_id"] for item in forced_batch["items"]] == [
        item["candidate_id"] for item in batch["items"]
    ]

    _use(FakeClient(fail=True))
    degraded = client.get(
        f"/negotiations/{negotiation_id}/me", headers=headers["a"]
    ).json()["question_batch"]
    assert degraded["wording_source"] == "template"
    assert degraded["wording_degraded"] is True
    assert degraded["wording_degraded_reason"] == "llm_unavailable"


def _reach_concern_stage(client):
    negotiation_id, headers = _start(client, round_limit=1)
    batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert _answer(client, negotiation_id, headers["a"], batch, acceptable=1).status_code == 200
    b_batch = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], b_batch, acceptable=2).status_code == 200
    closed = _close(client, negotiation_id, headers["a"], 1)
    result_version = closed.json()["result_version"]
    dcv = _me(client, negotiation_id, headers["b"])["decision_config_version"]
    rejected = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={"decision_config_version": dcv, "accept": False, "reason": None},
        headers=headers["b"],
    )
    assert rejected.status_code == 200, rejected.text
    return negotiation_id, headers, result_version


def test_concern_summary_is_assisted_then_manually_correctable(api) -> None:
    client, _ = api
    _use(FakeClient("成员担心其中一道菜不合口味，建议替换后再确认。"))
    negotiation_id, headers, result_version = _reach_concern_stage(client)
    created = client.post(
        f"/negotiations/{negotiation_id}/me/concerns",
        json={
            "result_version": result_version,
            "text": "这个组合里有一道我不能吃的菜，可能需要换一道。",
            "share_acknowledged": True,
        },
        headers=headers["b"],
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["summary_source"] == "llm"
    assert body["summary_degraded"] is False
    assert body["summary"] == "成员担心其中一道菜不合口味，建议替换后再确认。"
    assert body["text"] == "这个组合里有一道我不能吃的菜，可能需要换一道。"

    listed = client.get(
        f"/negotiations/{negotiation_id}/organizer/concerns", headers=headers["a"]
    ).json()
    assert listed[0]["summary_source"] == "llm"

    corrected = client.patch(
        f"/negotiations/{negotiation_id}/organizer/concerns/{listed[0]['concern_id']}/summary",
        json={"summary": "成员希望更换其中一道菜，未说明具体菜名。"},
        headers=headers["a"],
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["summary_source"] == "manual"
    assert corrected.json()["summary"] == "成员希望更换其中一道菜，未说明具体菜名。"


def test_concern_summary_degrades_when_llm_fails(api) -> None:
    client, _ = api
    _use(FakeClient(fail=True))
    negotiation_id, headers, result_version = _reach_concern_stage(client)
    created = client.post(
        f"/negotiations/{negotiation_id}/me/concerns",
        json={
            "result_version": result_version,
            "text": "我担心预算偏高。",
            "share_acknowledged": True,
        },
        headers=headers["b"],
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["summary"] is None
    assert body["summary_source"] == "none"
    assert body["summary_degraded"] is True
    assert body["summary_degraded_reason"] == "llm_unavailable"

