"""成员状态机、治理授权、本人残留权限与旧快照竞争回归。"""
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.engine.closing import begin_round_close, commit_round, compute_round
from app.engine.constraints_service import proposer_member_id
from app.engine.loading import active_answers, constraint_dtos, member_dtos, candidate_dtos
from app.engine.state import EngineError
from app.models.business import (
    Candidate, ConfirmedConstraint, Member, Negotiation, QuestionBatch,
    RemovalRequest, ResultConfirmation, utcnow,
)
from app.models.events import NegotiationEvent
from app.models.outbox import Outbox
from app.schemas.commands import NegotiationStatus
from app.tests.test_api_flow import _candidates, _me, _answer, _close


def group(client, count=4, *, start=True, constraints=None):
    body = client.post("/negotiations", json={
        "title": "membership", "initiator_name": "owner", "candidates": _candidates(),
        "budget_cents": 6000, "exact_count": 2, "round_limit": 6,
    }).json()
    nid = body["negotiation_id"]
    identities = [body]
    for index in range(1, count):
        response = client.post(f"/negotiations/{nid}/members", json={
            "invite_token": body["invite_token"], "display_name": f"member{index}",
        })
        assert response.status_code == 201, response.text
        identities.append(response.json())
    headers = [{"Authorization": f"Bearer {m['member_token']}"} for m in identities]
    ids = [m["member_id"] for m in identities]
    if start:
        for index, header in enumerate(headers):
            drafts = (constraints or {}).get(index, [])
            response = client.post(f"/negotiations/{nid}/me/constraints", headers=header,
                json={"constraints": drafts, "no_extra_bottomline": not drafts})
            assert response.status_code == 200, response.text
    return nid, headers, ids, body["invite_token"]


def version(client, nid, header):
    return client.get(f"/negotiations/{nid}/me/membership", headers=header).json()["membership_version"]


def request(client, nid, header, target, ver):
    return client.post(f"/negotiations/{nid}/members/{target}/removal-requests", headers=header,
        json={"expected_membership_version": ver, "reason": "cannot continue together"})


def vote(client, nid, header, rid, ver, approve=True):
    return client.post(f"/negotiations/{nid}/removal-requests/{rid}/me/authorization", headers=header,
        json={"expected_membership_version": ver, "approve": approve, "consequences_acknowledged": True})


def depart(client, nid, header, ver):
    return client.post(f"/negotiations/{nid}/me/leave", headers=header,
        json={"expected_membership_version": ver, "acknowledged": True})


@pytest.mark.parametrize("status", [s.value for s in NegotiationStatus])
def test_frozen_invitation_rejected_in_every_state(api, status):
    client, factory = api
    nid, headers, ids, invite = group(client)
    with factory() as db:
        n = db.get(Negotiation, nid)
        n.status = status
        # 即使重置当前轮次/回到底线收集，也不能解冻首次名单。
        n.round_no = 0
        db.commit()
    result = client.post(f"/negotiations/{nid}/members", json={
        "invite_token": invite, "display_name": "replacement",
    })
    assert result.status_code == 409
    with factory() as db:
        assert len(db.scalars(select(Member)).all()) == 4


@pytest.mark.parametrize("status", [s.value for s in NegotiationStatus])
def test_leave_in_every_negotiation_state(api, status):
    client, factory = api
    nid, headers, ids, _ = group(client)
    ver = version(client, nid, headers[0])
    with factory() as db:
        db.get(Negotiation, nid).status = status
        db.commit()
    result = depart(client, nid, headers[3], ver)
    terminal = status in {"FINALIZED", "CLOSED_WITH_CONCERN", "CLOSED_UNCONFIRMED"}
    assert result.status_code == (409 if terminal else 204), result.text
    with factory() as db:
        member = db.get(Member, ids[3])
        assert member.membership_status == ("ACTIVE" if terminal else "LEFT")
        assert member.active == terminal


def test_force_removal_requires_all_other_explicit_votes(api):
    client, factory = api
    nid, headers, ids, _ = group(client)
    ver = version(client, nid, headers[0])
    assert request(client, nid, headers[1], ids[3], ver).status_code == 403
    assert request(client, nid, headers[0], ids[0], ver).status_code == 403
    first = request(client, nid, headers[0], ids[3], ver)
    assert first.status_code == 200, first.text
    rid = first.json()["request_id"]
    assert request(client, nid, headers[0], ids[3], ver).json()["request_id"] == rid
    assert vote(client, nid, headers[3], rid, ver).status_code == 403
    assert _me(client, nid, headers[3])["question_batch"] is not None
    with factory() as db:
        old_config = db.get(Negotiation, nid).decision_config_version
    for index in (0, 1):
        assert vote(client, nid, headers[index], rid, ver).json()["status"] == "pending"
        assert vote(client, nid, headers[index], rid, ver).json()["status"] == "pending"
    assert vote(client, nid, headers[1], rid, ver, False).status_code == 409
    with factory() as db:
        assert db.get(Member, ids[3]).active
        assert db.get(Negotiation, nid).decision_config_version == old_config
    response = vote(client, nid, headers[2], rid, ver)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "effective"
    assert client.get(f"/negotiations/{nid}/public", headers=headers[3]).status_code == 401
    public = client.get(f"/negotiations/{nid}/public", headers=headers[0]).json()
    assert public["membership"]["removed_count"] == 1
    assert public["membership"]["participating_count"] == 3
    assert public["membership"]["membership_version"] == ver + 1
    assert vote(client, nid, headers[0], rid, ver).status_code == 409


@pytest.mark.parametrize("status", [s.value for s in NegotiationStatus])
def test_removal_effective_in_every_open_phase_but_never_after_close(api, status):
    client, factory = api
    nid, headers, ids, _ = group(client)
    ver = version(client, nid, headers[0])
    with factory() as db:
        db.get(Negotiation, nid).status = status
        db.commit()
    response = request(client, nid, headers[0], ids[3], ver)
    if status in {"FINALIZED", "CLOSED_WITH_CONCERN", "CLOSED_UNCONFIRMED"}:
        assert response.status_code == 409
        return
    assert response.status_code == 200, response.text
    rid = response.json()["request_id"]
    for header in headers[:3]:
        result = vote(client, nid, header, rid, ver)
        assert result.status_code == 200, result.text
    assert result.json()["status"] == "effective"


@pytest.mark.parametrize("resolution", ["reject", "cancel", "leave_target", "leave_voter", "join", "stop"])
def test_removal_authorization_lifecycle(api, resolution):
    client, factory = api
    nid, headers, ids, invite = group(client, start=resolution != "join")
    ver = version(client, nid, headers[0])
    rid = request(client, nid, headers[0], ids[3], ver).json()["request_id"]
    assert vote(client, nid, headers[0], rid, ver).status_code == 200
    if resolution == "reject":
        assert vote(client, nid, headers[1], rid, ver, False).json()["status"] == "rejected"
    elif resolution == "cancel":
        url = f"/negotiations/{nid}/removal-requests/{rid}/cancel"
        assert client.post(url, headers=headers[1], json={"expected_membership_version": ver}).status_code == 403
        assert client.post(url, headers=headers[0], json={"expected_membership_version": ver}).status_code == 204
    elif resolution.startswith("leave"):
        assert depart(client, nid, headers[3 if resolution == "leave_target" else 1], ver).status_code == 204
    elif resolution == "join":
        assert client.post(f"/negotiations/{nid}/members", json={
            "invite_token": invite, "display_name": "another",
        }).status_code == 201
    else:
        with factory() as db:
            db.get(Negotiation, nid).status = "BEST_EFFORT"
            db.commit()
        assert client.post(f"/negotiations/{nid}/organizer/decision", headers=headers[0], json={"action": "stop"}).status_code == 200
    assert vote(client, nid, headers[2], rid, ver).status_code == 409
    with factory() as db:
        r = db.get(RemovalRequest, rid)
        assert r.status in {"rejected", "cancelled", "expired"}
        assert db.get(Member, ids[3]).membership_status != "REMOVAL_REQUESTED"


def test_departed_constraints_scoped_access_and_shared_exclusions(api):
    client, factory = api
    drafts = [{"kind": "exclude_item", "candidate_ids": ["c01"]},
              {"kind": "opt_out", "candidate_ids": ["c02"]},
              {"kind": "require_count", "category": "荤菜", "min_count": 1}]
    # 使用有效类别，以确保退出前的替代要求可行。
    drafts[2]["category"] = _candidates()[0]["category"]
    nid, headers, ids, _ = group(client, constraints={2: [drafts[0]], 3: drafts})
    batch = _me(client, nid, headers[3])["question_batch"]
    assert _answer(client, nid, headers[3], batch).status_code == 200
    assert depart(client, nid, headers[3], version(client, nid, headers[0])).status_code == 204
    for path in ("public", "me", "me/history", "me/membership", "organizer/concerns"):
        assert client.get(f"/negotiations/{nid}/{path}", headers=headers[3]).status_code == 401
    assert _answer(client, nid, headers[3], batch).status_code == 401
    retained = client.get(f"/negotiations/{nid}/me/retained-constraints", headers=headers[3])
    assert retained.status_code == 200
    assert [c["kind"] for c in retained.json()["constraints"]] == ["exclude_item"]
    own = retained.json()["constraints"][0]["constraint_id"]
    with factory() as db:
        assert all(a.member_id != ids[3] for a in active_answers(db, nid))
        rows = db.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.negotiation_id == nid,
                ConfirmedConstraint.active.is_(True),
            )
        ).all()
        exclude_rows = [r for r in rows if r.kind == "exclude_item"]
        assert all(r.owner_member_id is None for r in exclude_rows), "整组避开的提出者只记在 payload"
        own_rows = [r for r in rows if proposer_member_id(r) == ids[3]]
        assert {r.kind for r in own_rows} == {"exclude_item"}
        other = next(
            r for r in exclude_rows if proposer_member_id(r) == ids[2]
        )
        other_id = other.constraint_id
    payload = {"expected_preference_version": retained.json()["preference_version"], "acknowledged": True}
    assert client.post(f"/negotiations/{nid}/me/retained-constraints/{other_id}/withdraw", headers=headers[3], json=payload).status_code == 404
    response = client.post(f"/negotiations/{nid}/me/retained-constraints/{own}/withdraw", headers=headers[3], json=payload)
    assert response.status_code == 200, response.text
    public = client.get(f"/negotiations/{nid}/public", headers=headers[0]).json()
    assert "c01" not in public["recommendation"]["candidate_ids"]
    assert client.post(f"/negotiations/{nid}/me/revoke-token", headers=headers[3]).status_code == 204
    assert client.get(f"/negotiations/{nid}/me/retained-constraints", headers=headers[3]).status_code == 401


def test_custom_constraint_id_cannot_claim_legacy_row(api):
    """客户端传入同名 ID 不能认领无来源的历史整组避开。"""
    client, factory = api
    nid, headers, ids, _ = group(client)
    with factory() as db:
        db.add(
            ConfirmedConstraint(
                negotiation_id=nid,
                constraint_id="shared_custom",
                owner_member_id=None,
                scope="public",
                kind="exclude_item",
                payload={"candidate_ids": ["c01"]},
                confirmed_version=0,
                active=True,
            )
        )
        db.commit()
    response = client.post(
        f"/negotiations/{nid}/me/constraints",
        headers=headers[1],
        json={
            "constraints": [
                {
                    "kind": "exclude_item",
                    "candidate_ids": ["c02"],
                    "constraint_id": "shared_custom",
                }
            ]
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "CONSTRAINT_ID_INVALID"
    with factory() as db:
        rows = db.scalars(
            select(ConfirmedConstraint).where(
                ConfirmedConstraint.negotiation_id == nid,
                ConfirmedConstraint.constraint_id == "shared_custom",
                ConfirmedConstraint.active.is_(True),
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].payload["candidate_ids"] == ["c01"]


def test_departure_invalidates_computing_snapshot_and_old_batches(api):
    client, factory = api
    nid, headers, ids, _ = group(client)
    old_batch = _me(client, nid, headers[1])["question_batch"]
    with factory() as stale:
        n = stale.get(Negotiation, nid)
        actor = stale.get(Member, ids[0])
        snapshot = begin_round_close(stale, n, actor)
        computation = compute_round(snapshot)
        assert depart(client, nid, headers[3], version(client, nid, headers[0])).status_code == 204
        # expire_on_commit=False：同一 Session 仍持有 CLOSING 旧对象，也必须重读 CAS。
        with pytest.raises(EngineError) as error:
            commit_round(stale, snapshot, computation)
        assert error.value.code == "ROUND_COMMIT_RACE"
    assert _answer(client, nid, headers[1], old_batch).status_code == 409
    with factory() as db:
        n = db.get(Negotiation, nid)
        assert n.membership_version == 5
        events = db.scalars(select(NegotiationEvent).where(NegotiationEvent.type == "MEMBER_LEFT")).all()
        assert len(events) == 1
        assert db.scalar(select(Outbox.id).where(Outbox.state_version == events[0].state_version))


def test_response_label_preserves_inputs_and_return(api):
    client, factory = api
    nid, headers, ids, _ = group(client)
    ver = version(client, nid, headers[0])
    url = f"/negotiations/{nid}/members/{ids[3]}/unresponsive"
    assert client.post(url, headers=headers[0], json={"expected_membership_version": ver}).status_code == 409
    with factory() as db:
        before = db.get(Negotiation, nid).decision_config_version
        batch = db.scalars(select(QuestionBatch).where(QuestionBatch.member_id == ids[3])).one()
        batch.deadline = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post(url, headers=headers[0], json={"expected_membership_version": ver}).status_code == 204
    with factory() as db:
        member = db.get(Member, ids[3])
        assert member.response_status == "UNRESPONSIVE" and member.active
        assert db.get(Negotiation, nid).decision_config_version == before
        assert len(member_dtos(db.scalars(select(Member).where(Member.active.is_(True))).all())) == 4
    assert client.post(f"/negotiations/{nid}/me/return", headers=headers[3]).status_code == 204
    assert client.get(f"/negotiations/{nid}/me/membership", headers=headers[3]).json()["response_status"] == "ACTIVE"


@pytest.mark.parametrize("count,target", [(1, 0), (2, 1), (3, 2), (4, 0)])
def test_too_few_members_or_initiator_leaving_closes_explicitly(api, count, target):
    client, factory = api
    nid, headers, ids, _ = group(client, count=count, start=False)
    assert depart(client, nid, headers[target], version(client, nid, headers[0])).status_code == 204
    with factory() as db:
        n = db.get(Negotiation, nid)
        assert n.status == "CLOSED_UNCONFIRMED" and n.active_result_version is None


def test_historical_rejection_survives_departure(api):
    client, factory = api
    nid, headers, ids, _ = group(client)
    with factory() as db:
        db.get(Negotiation, nid).round_limit = 1
        db.commit()
    outcome = _close(client, nid, headers[0], 1).json()
    rv = outcome["result_version"]
    dcv = _me(client, nid, headers[3])["decision_config_version"]
    assert client.post(f"/negotiations/{nid}/results/{rv}/me/confirmation", headers=headers[3],
        json={"decision_config_version": dcv, "accept": False}).status_code == 200
    assert depart(client, nid, headers[3], version(client, nid, headers[0])).status_code == 204
    public = client.get(f"/negotiations/{nid}/public", headers=headers[0]).json()
    assert public["membership"]["has_recorded_objection"] is True
    assert public["membership"]["left_count"] == 1
    assert public["membership"]["unconfirmed_count"] == 3
    with factory() as db:
        assert db.scalar(select(ResultConfirmation.id).where(ResultConfirmation.member_id == ids[3])) is not None


def test_response_or_removal_request_during_close_does_not_cancel_job(api):
    client, factory = api
    nid, headers, ids, _ = group(client)
    with factory() as stale:
        snapshot = begin_round_close(stale, stale.get(Negotiation, nid), stale.get(Member, ids[0]))
        assert request(client, nid, headers[0], ids[3], version(client, nid, headers[0])).status_code == 200
        result = commit_round(stale, snapshot, compute_round(snapshot))
        assert result["outcome"] == "changed"


def test_fewer_than_two_voters_cannot_force_removal(api):
    client, factory = api
    nid, headers, ids, _ = group(client, count=2, start=False)
    result = request(client, nid, headers[0], ids[1], version(client, nid, headers[0]))
    assert result.status_code == 409
    assert result.json()["code"] == "REMOVAL_QUORUM_REQUIRED"
    with factory() as db:
        assert db.get(Member, ids[1]).active


def test_pending_bottomline_stays_pending_and_prevents_new_result(api):
    client, factory = api
    nid, headers, ids, _ = group(client, start=False)
    assert depart(client, nid, headers[3], version(client, nid, headers[0])).status_code == 204
    with factory() as db:
        n = db.get(Negotiation, nid)
        assert n.status == "COLLECTING_BOTTOMLINE" and n.active_result_version is None
        assert all(m.bottomline_status == "pending" for m in db.scalars(select(Member)).all())


def test_stale_authenticated_member_cannot_replay_answer_after_removal(api):
    from app.engine.answers import submit_answer
    from app.schemas.commands import AnswerCommand
    client, factory = api
    nid, headers, ids, _ = group(client)
    batch = _me(client, nid, headers[3])["question_batch"]
    with factory() as stale:
        actor = stale.get(Member, ids[3])
        ver = version(client, nid, headers[0])
        rid = request(client, nid, headers[0], ids[3], ver).json()["request_id"]
        for header in headers[:3]:
            assert vote(client, nid, header, rid, ver).status_code == 200
        with pytest.raises(EngineError) as error:
            submit_answer(stale, nid, actor, AnswerCommand(
                question_batch_id=batch["question_batch_id"], request_id="stale-request-0001", mode="skip",
            ))
        assert error.value.code == "UNAUTHORIZED"


def test_rebuild_overwrites_departed_private_snapshot_and_survives_cache_loss(api):
    from app.tests.test_outbox_worker import FakeRedis
    from app.workers import outbox_projection as worker
    client, factory = api
    nid, headers, ids, _ = group(client)
    cache = FakeRedis()
    with factory() as db:
        worker.project_negotiation(db, cache, nid)
    key = worker.member_key(nid, ids[3])
    assert "question_batch" in cache.store[key]["view"]
    assert depart(client, nid, headers[3], version(client, nid, headers[0])).status_code == 204
    with factory() as db:
        worker.project_negotiation(db, cache, nid)
    assert json.loads(cache.store[key]["view"]) == {"membership_status": "LEFT", "access": "retained_constraints_only"}
    before = dict(cache.store)
    cache.store.clear()
    with factory() as db:
        worker.project_negotiation(db, cache, nid)
    assert cache.store == before


def test_membership_change_survives_compute_failure_and_can_resume(api, monkeypatch):
    from app.engine import closing
    client, factory = api
    nid, headers, ids, _ = group(client)
    ver = version(client, nid, headers[0])
    original = closing.compute_round
    def fail(snapshot, **kwargs):
        raise RuntimeError("simulated process failure before commit")
    monkeypatch.setattr(closing, "compute_round", fail)
    with pytest.raises(RuntimeError):
        depart(client, nid, headers[3], ver)
    with factory() as db:
        assert not db.get(Member, ids[3]).active
        negotiation = db.get(Negotiation, nid)
        assert negotiation.membership_version == ver + 1
        assert negotiation.status == "CLOSING"
        round_no = negotiation.round_no
        assert db.scalars(select(NegotiationEvent).where(NegotiationEvent.type == "MEMBER_LEFT")).first() is not None
    monkeypatch.setattr(closing, "compute_round", original)
    response = _close(client, nid, headers[0], round_no)
    assert response.status_code == 200, response.text
    assert response.json()["negotiation_status"] != "CLOSING"


def test_migration_upgrade_backfills_freeze_and_downgrade(tmp_path, monkeypatch):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text, inspect
    from app.core.config import settings
    url = f"sqlite+pysqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "c4d9a1f2b7e3")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO negotiation
            (id,title,status,exact_count,budget_cents,round_limit,round_no,state_version,
             constraint_version,decision_config_version,algorithm_profile,config_json,created_at,updated_at)
            VALUES (1,'old','ASKING',2,6000,6,1,1,1,1,'mean_fair_v1','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        connection.execute(text("""INSERT INTO member
            (id,negotiation_id,display_name,token_hash,role,active,preference_version,bottomline_status,created_at)
            VALUES (1,1,'old','old','member',0,1,'pending',CURRENT_TIMESTAMP)"""))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT membership_frozen FROM negotiation")) == 1
        assert connection.scalar(text("SELECT token_revoked FROM member")) == 1
        assert connection.scalar(text("SELECT membership_status FROM member")) == "ACTIVE"
    assert "removal_request" in inspect(engine).get_table_names()
    command.downgrade(config, "c4d9a1f2b7e3")
    assert "removal_request" not in inspect(engine).get_table_names()
    assert "membership_version" not in {c["name"] for c in inspect(engine).get_columns("negotiation")}
    command.upgrade(config, "head")
    engine.dispose()
