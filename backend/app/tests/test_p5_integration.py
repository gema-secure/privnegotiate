"""P5 封轮产物落入审计：挑战池、保守界与下一轮派题计划。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.business import RoundResult
from app.tests.test_api_flow import _answer, _close, _me, _start


def test_round_result_persists_p5_diagnostics(api) -> None:
    client, factory = api
    negotiation_id, headers = _start(client, round_limit=2)

    for key in ("a", "b"):
        batch = _me(client, negotiation_id, headers[key])["question_batch"]
        assert batch is not None
        assert _answer(client, negotiation_id, headers[key], batch, acceptable=1).status_code == 200

    closed = _close(client, negotiation_id, headers["a"], 1)
    assert closed.status_code == 200, closed.text
    assert closed.json()["negotiation_status"] == "ASKING"

    with factory() as session:
        row = session.scalars(
            select(RoundResult).where(RoundResult.negotiation_id == negotiation_id)
        ).one()
        diagnostics = row.diagnostics

    pool = diagnostics["challenge_pool"]
    assert pool["incumbent_ids"] == list(row.candidate_ids)
    assert len(pool["challengers"]) >= 4
    assert any(item["role"] == "personal" for item in pool["challengers"])
    assert pool["upper_bound"] is not None

    report = diagnostics["bound_report"]
    assert 0.0 <= report["epsilon_safe"] <= 1.0
    assert report["bound_source"] in {"solver", "universal"}
    assert report["unknown_pairs"] > 0

    plan = diagnostics["question_plan"]
    assert plan["round_no"] == 2
    assert plan["members"]
    asked = {
        item["candidate_id"]
        for member in plan["members"]
        for item in member["items"]
    }
    assert asked
    assert all(
        item["reason"] in {"challenger", "coverage", "explore"}
        for member in plan["members"]
        for item in member["items"]
    )
    assert diagnostics["stopping_reason"] == "CONTINUE"
    audit = diagnostics["audit"]
    assert audit["software_version"]
    assert audit["algorithm_profile"] == "mean_fair_v1"
    assert len(audit["input_checksum"]) == 64
    assert len(audit["result_checksum"]) == 64
    assert audit["stopping_reason"] == "CONTINUE"
    assert audit["seed"] == 0
