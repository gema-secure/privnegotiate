"""P4 入口验收：多成员同轮作答、幂等、截止竞争、版本失效、顾虑闭环。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.engine.batches import mark_batches_closed, reopen_batches
from app.engine.closing import PreviousResult, RoundSnapshot, _stable_count
from app.models.business import (
    ConfirmedConstraint,
    Member,
    Negotiation,
    QuestionBatch,
    utcnow,
)
from app.solver.mean_fair import SolveResult, SolveStatus


def _candidates():
    return [
        {
            "candidate_id": f"c{index:02d}",
            "name": f"菜品{index}",
            "cost_cents": 800 + index * 100,
            "category": "main" if index <= 7 else "side",
            "confirmed_attributes": {},
        }
        for index in range(1, 14)
    ]


def _start(client, round_limit: int = 2):
    created = client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "candidates": _candidates(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": round_limit,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    negotiation_id = body["negotiation_id"]
    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    headers = {
        "a": {"Authorization": f"Bearer {body['member_token']}"},
        "b": {"Authorization": f"Bearer {joined.json()['member_token']}"},
    }
    for key in ("a", "b"):
        response = client.post(
            f"/negotiations/{negotiation_id}/me/constraints",
            json={
                "constraints": [],
                "no_extra_bottomline": True,
                "expected_preference_version": 1,
            },
            headers=headers[key],
        )
        assert response.status_code == 200, response.text
    return negotiation_id, headers


def _me(client, negotiation_id: int, headers):
    response = client.get(f"/negotiations/{negotiation_id}/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _answer(client, negotiation_id, headers, batch, mode="explicit", acceptable=1):
    displayed = [item["candidate_id"] for item in batch["items"]]
    payload = {
        "question_batch_id": batch["question_batch_id"],
        "request_id": f"req-{batch['question_batch_id']}-{headers['Authorization'][-8:]}",
        "mode": mode,
    }
    if mode == "explicit":
        payload["evaluated_ids"] = displayed
        payload["acceptable_ids"] = displayed[:acceptable] if acceptable else []
        payload["top_id"] = displayed[0] if acceptable else None
    return client.post(
        f"/negotiations/{negotiation_id}/me/answers", json=payload, headers=headers
    )


def _close(client, negotiation_id, headers, round_no):
    return client.post(
        f"/negotiations/{negotiation_id}/rounds/{round_no}/close", headers=headers
    )


def test_multi_member_round_idempotency_and_finalize(api):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)

    me = _me(client, negotiation_id, headers["a"])
    assert me["status"] == "ASKING"
    assert me["round_no"] == 1
    batch = me["question_batch"]
    assert batch is not None and len(batch["items"]) == 4

    first = _answer(client, negotiation_id, headers["a"], batch, acceptable=1)
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False

    replay = _answer(client, negotiation_id, headers["a"], batch, acceptable=1)
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True

    batch_b = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert batch_b is not None
    response_b = _answer(client, negotiation_id, headers["b"], batch_b, acceptable=0)
    assert response_b.status_code == 200, response_b.text

    closed = _close(client, negotiation_id, headers["a"], 1)
    assert closed.status_code == 200, closed.text
    assert closed.json()["outcome"] == "changed"
    assert closed.json()["negotiation_status"] == "ASKING"
    assert closed.json()["result_version"] == 1
    assert closed.json()["stable_count"] == 1

    second_batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert second_batch is not None and second_batch["round_no"] == 2
    assert _answer(client, negotiation_id, headers["a"], second_batch, "skip").status_code == 200
    second_b_for_b = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], second_b_for_b, "skip").status_code == 200

    settled = _close(client, negotiation_id, headers["a"], 2)
    assert settled.status_code == 200, settled.text
    assert settled.json()["negotiation_status"] == "BEST_EFFORT"

    result_version = settled.json()["result_version"]
    public = client.get(f"/negotiations/{negotiation_id}/public", headers=headers["a"]).json()
    assert public["recommendation"]["result_version"] == result_version
    assert public["pending_action"] == "waiting_confirmation"

    my_a = _me(client, negotiation_id, headers["a"])
    accepted = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={
            "decision_config_version": my_a["decision_config_version"],
            "accept": True,
            "reason": None,
        },
        headers=headers["a"],
    )
    assert accepted.status_code == 200, accepted.text
    assert _me(client, negotiation_id, headers["a"])["confirmation"]["decision"] == "accept"

    final = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={
            "decision_config_version": _me(client, negotiation_id, headers["b"])[
                "decision_config_version"
            ],
            "accept": True,
            "reason": None,
        },
        headers=headers["b"],
    )
    assert final.status_code == 200, final.text
    public = client.get(f"/negotiations/{negotiation_id}/public", headers=headers["a"]).json()
    assert public["status"] == "FINALIZED"
    assert public["pending_action"] == "none"
    assert "member_token" not in public


def test_mid_round_join_does_not_skip_current_round(api):
    client, factory = api
    created = client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "candidates": _candidates(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": 3,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    negotiation_id = body["negotiation_id"]
    headers = {"a": {"Authorization": f"Bearer {body['member_token']}"}}
    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    headers["b"] = {"Authorization": f"Bearer {joined.json()['member_token']}"}
    for key in ("a", "b"):
        response = client.post(
            f"/negotiations/{negotiation_id}/me/constraints",
            json={"constraints": [], "no_extra_bottomline": True},
            headers=headers[key],
        )
        assert response.status_code == 200, response.text

    me = _me(client, negotiation_id, headers["a"])
    assert me["round_no"] == 1
    assert _answer(client, negotiation_id, headers["a"], me["question_batch"]).status_code == 200
    decision_version = _me(client, negotiation_id, headers["b"])["decision_config_version"]

    third = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "阿岚"},
    )
    assert third.status_code == 201, third.text
    headers["c"] = {"Authorization": f"Bearer {third.json()['member_token']}"}
    assert _me(client, negotiation_id, headers["b"])["decision_config_version"] == (
        decision_version + 1
    )

    confirmed = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [], "no_extra_bottomline": True},
        headers=headers["c"],
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["round_no"] == 1
    assert confirmed.json()["status"] in {"ASKING", "ANSWERING"}

    with factory() as session:
        skipped = session.scalars(
            select(QuestionBatch).where(
                QuestionBatch.negotiation_id == negotiation_id,
                QuestionBatch.round_no == 2,
            )
        ).all()
        assert skipped == []

    b_batch = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], b_batch).status_code == 200
    closed = _close(client, negotiation_id, headers["a"], 1)
    assert closed.status_code == 200, closed.text
    assert closed.json()["stable_count"] == 1

    newcomer = _me(client, negotiation_id, headers["c"])
    assert newcomer["round_no"] == 2
    assert newcomer["question_batch"] is not None
    assert newcomer["question_batch"]["round_no"] == 2


def test_mid_round_bottomline_confirm_does_not_advance_round(api):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)
    me = _me(client, negotiation_id, headers["a"])
    assert me["round_no"] == 1

    response = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [], "no_extra_bottomline": True},
        headers=headers["a"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["round_no"] == 1
    assert response.json()["status"] == "ASKING"

    with factory() as session:
        skipped = session.scalars(
            select(QuestionBatch).where(
                QuestionBatch.negotiation_id == negotiation_id,
                QuestionBatch.round_no == 2,
            )
        ).all()
        assert skipped == []


def test_deadline_race_and_late_answer_rejected(api):
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=1)
    batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert _answer(client, negotiation_id, headers["a"], batch, acceptable=1).status_code == 200
    batch_b = _me(client, negotiation_id, headers["b"])["question_batch"]

    with factory() as session:
        rows = session.scalars(
            select(QuestionBatch).where(QuestionBatch.negotiation_id == negotiation_id)
        ).all()
        for row in rows:
            row.deadline = utcnow() - timedelta(seconds=5)
        session.commit()

    closed = _close(client, negotiation_id, headers["b"], 1)
    assert closed.status_code == 200, closed.text
    assert closed.json()["negotiation_status"] == "BEST_EFFORT"

    late = _answer(client, negotiation_id, headers["b"], batch_b, acceptable=1)
    assert late.status_code == 409
    assert late.json()["code"] in {"ANSWER_WINDOW_CLOSED", "BATCH_CLOSED"}


def test_confirmation_binds_rule_version_and_revision_invalidates(api):
    client, _ = api
    negotiation_id, headers = _start(client, round_limit=1)
    batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert _answer(client, negotiation_id, headers["a"], batch, acceptable=2).status_code == 200
    b_batch = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], b_batch, acceptable=1).status_code == 200

    closed = _close(client, negotiation_id, headers["a"], 1)
    assert closed.json()["negotiation_status"] == "BEST_EFFORT"
    result_version = closed.json()["result_version"]
    dcv = _me(client, negotiation_id, headers["a"])["decision_config_version"]

    stale = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={"decision_config_version": dcv + 1, "accept": True, "reason": None},
        headers=headers["b"],
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "RULE_VERSION_STALE"

    accepted = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={"decision_config_version": dcv, "accept": True, "reason": None},
        headers=headers["a"],
    )
    assert accepted.status_code == 200
    assert _me(client, negotiation_id, headers["a"])["confirmation"] is not None

    revised = client.post(
        f"/negotiations/{negotiation_id}/me/revisions",
        json={
            "request_id": "rev-00000001",
            "candidate_id": batch["items"][0]["candidate_id"],
            "acceptable": False,
        },
        headers=headers["a"],
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "ASKING"
    assert revised.json()["decision_config_version"] > dcv
    assert revised.json()["confirmation"] is None

    stale_again = client.post(
        f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
        json={"decision_config_version": dcv, "accept": True, "reason": None},
        headers=headers["b"],
    )
    assert stale_again.status_code == 409
    assert stale_again.json()["code"] in {"RULE_VERSION_STALE", "NOT_IN_CONFIRMATION"}


def test_concern_flow_and_initiator_only(api):
    client, _ = api
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
    assert rejected.status_code == 200
    assert _me(client, negotiation_id, headers["b"])["status"] == "CONCERN_COLLECTING"

    forbidden = client.get(
        f"/negotiations/{negotiation_id}/organizer/concerns", headers=headers["b"]
    )
    assert forbidden.status_code == 403

    concern = client.post(
        f"/negotiations/{negotiation_id}/me/concerns",
        json={
            "result_version": result_version,
            "text": "这个组合里有一道我不能吃的菜，可能需要换一道。",
            "share_acknowledged": True,
        },
        headers=headers["b"],
    )
    assert concern.status_code == 200, concern.text

    listed = client.get(
        f"/negotiations/{negotiation_id}/organizer/concerns", headers=headers["a"]
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert set(payload[0]) == {
        "concern_id",
        "result_version",
        "text",
        "summary",
        "summary_source",
        "summary_degraded",
        "summary_degraded_reason",
        "status",
        "created_at",
    }

    continued = client.post(
        f"/negotiations/{negotiation_id}/organizer/decision",
        json={
            "action": "continue",
            "concern_id": payload[0]["concern_id"],
            "extra_rounds": 1,
            "public_adjustment": None,
        },
        headers=headers["a"],
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["negotiation_status"] in {"ASKING", "BEST_EFFORT"}


def test_initiator_stop_closes_with_concern(api):
    client, _ = api
    negotiation_id, headers = _start(client, round_limit=1)
    batch = _me(client, negotiation_id, headers["a"])["question_batch"]
    assert _answer(client, negotiation_id, headers["a"], batch, acceptable=1).status_code == 200
    b_batch = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], b_batch, acceptable=2).status_code == 200
    closed = _close(client, negotiation_id, headers["a"], 1)
    result_version = closed.json()["result_version"]
    dcv = _me(client, negotiation_id, headers["b"])["decision_config_version"]
    assert (
        client.post(
            f"/negotiations/{negotiation_id}/results/{result_version}/me/confirmation",
            json={"decision_config_version": dcv, "accept": False, "reason": None},
            headers=headers["b"],
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/negotiations/{negotiation_id}/me/concerns",
            json={
                "result_version": result_version,
                "text": "我的底线被触及了。",
                "share_acknowledged": True,
            },
            headers=headers["b"],
        ).status_code
        == 200
    )
    stopped = client.post(
        f"/negotiations/{negotiation_id}/organizer/decision",
        json={"action": "stop", "concern_id": None},
        headers=headers["a"],
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["negotiation_status"] == "CLOSED_WITH_CONCERN"


def _snapshot(previous, *, constraint_version=1, decision_config_version=1, new_evidence=True):
    return RoundSnapshot(
        negotiation_id=1,
        round_no=2,
        state_version=1,
        constraint_version=constraint_version,
        decision_config_version=decision_config_version,
        input_event_cutoff=10,
        round_limit=5,
        config={},
        candidates=(),
        members=(),
        constraints=(),
        answers=(),
        previous=previous,
        new_explicit_evidence=new_evidence,
    )


def test_stability_counter_only_advances_on_verified_repeat():
    optimal = SolveResult(
        status=SolveStatus.OPTIMAL,
        candidate_ids=("c01", "c02"),
        tie_break_complete=True,
        config_version=1,
    )
    assert _stable_count(_snapshot(None), optimal) == 1

    first = PreviousResult(
        result_version=1,
        candidate_ids=("c02", "c01"),
        stable_count=1,
        input_event_cutoff=5,
        constraint_version=1,
        decision_config_version=1,
        solver_status="OPTIMAL",
        tie_break_complete=True,
    )
    assert _stable_count(_snapshot(first), optimal) == 2
    assert _stable_count(_snapshot(first, new_evidence=False), optimal) == 1
    assert _stable_count(_snapshot(first, constraint_version=2), optimal) == 1

    feasible_only = SolveResult(
        status=SolveStatus.FEASIBLE,
        candidate_ids=("c01", "c02"),
        tie_break_complete=False,
        config_version=1,
    )
    assert _stable_count(_snapshot(first), feasible_only) == 0


def test_retry_or_review_reconfirm_does_not_advance_round(api):
    """RETRY_OR_REVIEW 期间本轮题包仍有效，成员重存底线不得开新一轮。

    状态构造对应 closing.py:471-472——有效答案不足时置 RETRY_OR_REVIEW
    并重开本轮题包；answers.py:67 也允许该状态下继续作答，
    因此本轮在语义上仍然有效。

    缺陷：me.py:70 的 open_round 只认 ASKING/ANSWERING，
    该状态下为 False；而 me.py:71 的"全体非 pending"在成员均已确认后恒为真，
    于是 start_round 被误触发，batches.py:60-66 会关掉所有 issued 批次
    （含刚重开的本轮题包），round_no 前移，本轮被静默跳过并消耗一次 round_limit。
    """
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=3)

    started = _me(client, negotiation_id, headers["a"])
    assert started["round_no"] == 1
    assert started["question_batch"] is not None

    # 复现"封轮后证据不足、本轮被重开"的真实状态
    with factory() as session:
        negotiation = session.get(Negotiation, negotiation_id)
        assert negotiation is not None
        mark_batches_closed(session, negotiation_id, 1)
        session.flush()
        negotiation.status = "RETRY_OR_REVIEW"
        reopen_batches(session, negotiation_id, 1)
        session.commit()

    reopened = _me(client, negotiation_id, headers["a"])
    assert reopened["status"] == "RETRY_OR_REVIEW"
    assert reopened["round_no"] == 1
    assert reopened["question_batch"] is not None

    # 成员重新提交底线：此时全员本就非 pending
    response = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [], "no_extra_bottomline": True},
        headers=headers["b"],
    )
    assert response.status_code == 200, response.text

    after = _me(client, negotiation_id, headers["a"])
    assert after["round_no"] == 1, "重存底线不应推进轮次"
    assert after["status"] == "RETRY_OR_REVIEW", "本轮应仍处于待补充证据状态"
    assert after["question_batch"] is not None, "本轮题包应仍可作答"

    with factory() as session:
        next_round = session.scalars(
            select(QuestionBatch).where(
                QuestionBatch.negotiation_id == negotiation_id,
                QuestionBatch.round_no == 2,
            )
        ).all()
        assert next_round == [], "不应生成第 2 轮题包"

        round_one = session.scalars(
            select(QuestionBatch).where(
                QuestionBatch.negotiation_id == negotiation_id,
                QuestionBatch.round_no == 1,
            )
        ).all()
        assert round_one, "第 1 轮题包不应丢失"
        assert all(row.status == "issued" for row in round_one), (
            "第 1 轮题包应保持 issued，可继续作答"
        )


def test_group_exclude_is_public_and_applies_to_every_member(api):
    """整组避开以公共约束落库并全员生效；本人不参与仍是成员级独立路径。"""
    client, factory = api
    created = client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "candidates": _candidates(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": 3,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    negotiation_id = body["negotiation_id"]
    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    headers = {
        "a": {"Authorization": f"Bearer {body['member_token']}"},
        "b": {"Authorization": f"Bearer {joined.json()['member_token']}"},
    }

    group_excluded = [f"c{index:02d}" for index in range(4, 14)]
    response = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={
            "constraints": [
                {"kind": "exclude_item", "candidate_ids": group_excluded},
                {"kind": "opt_out", "candidate_ids": ["c03"]},
            ],
            "no_extra_bottomline": False,
        },
        headers=headers["a"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COLLECTING_BOTTOMLINE"

    with factory() as session:
        rows = session.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.negotiation_id == negotiation_id,
                ConfirmedConstraint.active.is_(True),
            )
        ).all()
        initiator = session.scalars(
            select(Member).where(
                Member.negotiation_id == negotiation_id,
                Member.display_name == "组长",
            )
        ).one()

    exclude_rows = [row for row in rows if row.kind == "exclude_item"]
    opt_out_rows = [row for row in rows if row.kind == "opt_out"]
    assert len(exclude_rows) == 1, "整组避开应作为一条公共约束落库"
    assert exclude_rows[0].scope == "public"
    assert exclude_rows[0].owner_member_id is None
    assert set(exclude_rows[0].payload["candidate_ids"]) == set(group_excluded)
    assert len(opt_out_rows) == 1, "本人不参与应保持成员级约束"
    assert opt_out_rows[0].scope == "member"
    assert opt_out_rows[0].owner_member_id == initiator.id

    finished = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={
            "constraints": [],
            "no_extra_bottomline": True,
            "expected_preference_version": 1,
        },
        headers=headers["b"],
    )
    assert finished.status_code == 200, finished.text

    batch_a = _me(client, negotiation_id, headers["a"])["question_batch"]
    batch_b = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert batch_a is not None and batch_b is not None
    ids_a = {item["candidate_id"] for item in batch_a["items"]}
    ids_b = {item["candidate_id"] for item in batch_b["items"]}
    assert ids_a.isdisjoint(group_excluded), "整组避开应对本人生效"
    assert ids_b.isdisjoint(group_excluded), "整组避开应对其他成员同样生效"
    assert ids_a == {"c01", "c02"}, "本人不参与只压缩本人的候选池"
    assert ids_b == {"c01", "c02", "c03"}, "本人不参与不得污染其他成员"


def test_resubmitting_bottomline_replaces_public_group_exclude(api):
    """成员回头改底线时，旧的公共整组避开必须被替换而不是叠加。"""
    client, factory = api
    created = client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "candidates": _candidates(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": 3,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    negotiation_id = body["negotiation_id"]
    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    headers = {
        "a": {"Authorization": f"Bearer {body['member_token']}"},
        "b": {"Authorization": f"Bearer {joined.json()['member_token']}"},
    }

    payload = {
        "constraints": [
            {"kind": "exclude_item", "candidate_ids": ["c04", "c05"]},
            {"kind": "opt_out", "candidate_ids": ["c03"]},
        ],
        "no_extra_bottomline": False,
    }
    for _ in range(2):
        saved = client.post(
            f"/negotiations/{negotiation_id}/me/constraints",
            json=payload,
            headers=headers["a"],
        )
        assert saved.status_code == 200, saved.text

    with factory() as session:
        rows = session.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.negotiation_id == negotiation_id,
                ConfirmedConstraint.active.is_(True),
            )
        ).all()
    exclude_rows = [row for row in rows if row.kind == "exclude_item"]
    opt_out_rows = [row for row in rows if row.kind == "opt_out"]
    assert len(exclude_rows) == 1, "公共整组避开只应保留一条 active 记录"
    assert exclude_rows[0].scope == "public"
    assert exclude_rows[0].owner_member_id is None
    assert len(opt_out_rows) == 1, "成员级约束也只应保留一条 active 记录"

    finished = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [], "no_extra_bottomline": True},
        headers=headers["b"],
    )
    assert finished.status_code == 200, finished.text
    state = _me(client, negotiation_id, headers["a"])
    assert state["status"] == "ASKING", "重复提交底线后仍应正常开局"
    assert state["round_no"] == 1


def test_continue_without_new_questions_is_explicit(api):
    """没有新题可发时，+1 轮不得静默成功；调整公共条件仍须落库。"""
    client, factory = api
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
    negotiation_id = body["negotiation_id"]
    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    headers = {
        "a": {"Authorization": f"Bearer {body['member_token']}"},
        "b": {"Authorization": f"Bearer {joined.json()['member_token']}"},
    }

    excluded = [f"c{index:02d}" for index in range(3, 14)]
    saved = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [{"kind": "exclude_item", "candidate_ids": excluded}]},
        headers=headers["a"],
    )
    assert saved.status_code == 200, saved.text
    finished = client.post(
        f"/negotiations/{negotiation_id}/me/constraints",
        json={"constraints": [], "no_extra_bottomline": True},
        headers=headers["b"],
    )
    assert finished.status_code == 200, finished.text

    for key in ("a", "b"):
        batch = _me(client, negotiation_id, headers[key])["question_batch"]
        assert batch is not None
        assert {item["candidate_id"] for item in batch["items"]} == {"c01", "c02"}
        assert _answer(client, negotiation_id, headers[key], batch, acceptable=1).status_code == 200

    closed = _close(client, negotiation_id, headers["a"], 1)
    assert closed.status_code == 200, closed.text
    assert closed.json()["negotiation_status"] == "BEST_EFFORT"

    continue_cmd = {
        "action": "continue",
        "extra_rounds": 1,
        "public_adjustment": None,
    }
    rejected = client.post(
        f"/negotiations/{negotiation_id}/organizer/decision",
        json=continue_cmd,
        headers=headers["a"],
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "NO_QUESTIONS_TO_ASK"

    with factory() as session:
        negotiation = session.get(Negotiation, negotiation_id)
        assert negotiation.round_limit == 2, "没有发出新轮时不得静默增加轮数"
        assert negotiation.status == "BEST_EFFORT"

    adjusted = client.post(
        f"/negotiations/{negotiation_id}/organizer/decision",
        json={
            "action": "continue",
            "extra_rounds": 1,
            "public_adjustment": {"budget_cents": 1900},
        },
        headers=headers["a"],
    )
    assert adjusted.status_code == 200, adjusted.text
    outcome = adjusted.json()
    assert outcome["round_issued"] is False
    assert outcome["outcome"] == "no_new_questions"
    assert outcome["message"]
    assert outcome["round_no"] == 1

    public = client.get(
        f"/negotiations/{negotiation_id}/public", headers=headers["a"]
    ).json()
    assert public["conditions"]["budget_cents"] == 1900, "条件调整必须真实落库"
    assert public["conditions"]["round_limit"] == 2, "未发出新轮不得追加轮数"
    assert public["status"] == "BEST_EFFORT"

    with factory() as session:
        budgets = session.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.negotiation_id == negotiation_id,
                ConfirmedConstraint.kind == "budget",
                ConfirmedConstraint.active.is_(True),
            )
        ).all()
        assert len(budgets) == 1
        assert budgets[0].payload["amount_cents"] == 1900
