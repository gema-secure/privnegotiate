"""成员共享投影的访问边界与字段白名单回归。"""

import pytest
from sqlalchemy import select

from app.models.business import Member, QuestionAnswer
from app.tests.test_api_flow import _answer, _me, _start


@pytest.mark.parametrize("authorization", [None, "Basic invalid", "Bearer ", "Bearer invalid"])
def test_public_requires_valid_member_token(api, authorization):
    client, _ = api
    negotiation_id, _ = _start(client)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get(f"/negotiations/{negotiation_id}/public", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert "title" not in response.json()


def test_public_rejects_other_negotiation_and_invite_token(api):
    client, _ = api
    first_id, first_headers = _start(client)
    second_id, _ = _start(client)
    response = client.get(
        f"/negotiations/{second_id}/public", headers=first_headers["a"]
    )
    assert response.status_code == 401
    assert "candidates" not in response.json()
    # 有效成员凭证访问不存在的编号也不能用于探测协商是否存在。
    missing = client.get(
        "/negotiations/999999/public", headers=first_headers["a"]
    )
    assert missing.status_code == 401
    from app.engine.setup import invite_token
    invited = client.get(
        f"/negotiations/{first_id}/public",
        headers={"Authorization": f"Bearer {invite_token(first_id)}"},
    )
    assert invited.status_code == 401


def test_public_rejects_inactive_member(api):
    client, factory = api
    negotiation_id, headers = _start(client)
    with factory() as db:
        member = db.scalars(
            select(Member).where(
                Member.negotiation_id == negotiation_id, Member.role == "member"
            )
        ).one()
        member.active = False
        db.commit()
    response = client.get(
        f"/negotiations/{negotiation_id}/public", headers=headers["b"]
    )
    assert response.status_code == 401
    assert client.get(
        f"/negotiations/{negotiation_id}/public", headers=headers["a"]
    ).status_code == 200


def test_public_whitelist_is_same_for_member_and_initiator(api):
    client, factory = api
    negotiation_id, headers = _start(client)
    batch = _me(client, negotiation_id, headers["b"])["question_batch"]
    assert _answer(client, negotiation_id, headers["b"], batch).status_code == 200
    # 让私有记录确有内容，避免空库字段检查漏掉条件性泄露。
    secret = "private-answer-reason-do-not-project"
    with factory() as db:
        answer = db.scalars(select(QuestionAnswer)).one()
        answer.reason = secret
        db.commit()
    responses = [
        client.get(f"/negotiations/{negotiation_id}/public", headers=headers[key])
        for key in ("a", "b")
    ]
    for response in responses:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert set(data) == {
            "negotiation_id", "title", "status", "round_no", "conditions",
            "candidates", "recommendation", "recommendation_pending_reason",
            "pending_action",
        }
        assert set(data["conditions"]) == {
            "budget_cents", "exact_count", "round_limit", "category_limits",
            "condition_version",
        }
        for candidate in data["candidates"]:
            assert set(candidate) == {
                "candidate_id", "name", "cost_cents", "category", "confirmed_attributes",
            }
        if data["recommendation"] is not None:
            assert set(data["recommendation"]) == {
                "result_version", "candidate_ids", "total_cost_cents", "source", "is_baseline",
            }
        assert secret not in response.text
        assert "组长" not in response.text and "小林" not in response.text
        assert headers["a"]["Authorization"][7:] not in response.text
        assert headers["b"]["Authorization"][7:] not in response.text
    assert responses[0].json() == responses[1].json()
