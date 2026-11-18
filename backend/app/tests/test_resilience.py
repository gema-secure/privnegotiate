from dataclasses import replace
from datetime import timedelta
import base64

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.engine import closing, feasibility
from app.engine.state import EngineError
from app.models.business import Member, Negotiation, RoundResult
from app.models.events import NegotiationEvent
from app.tests.test_api_flow import _start, _close


def test_production_rejects_default_and_short_invite_secrets():
    for secret in ("dev-only-invite-secret", "", "short", " " * 40):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, app_env="prod", invite_token_secret=secret)
    assert Settings(_env_file=None, app_env="prod", invite_token_secret="unique-test-secret-" * 3)


def test_crashed_close_resumes_same_cutoff_and_only_commits_once(api):
    client, factory = api
    nid, headers = _start(client)
    with factory() as db:
        negotiation = db.get(Negotiation, nid)
        actor = db.get(Member, negotiation.owner_member_id)
        first = closing.begin_round_close(db, negotiation, actor)
    with factory() as db:
        resumed = closing.begin_round_close(db, db.get(Negotiation, nid), db.get(Member, actor.id))
        assert resumed == first
        assert len(db.scalars(select(NegotiationEvent).where(
            NegotiationEvent.negotiation_id == nid, NegotiationEvent.type == "ROUND_CLOSING")).all()) == 1
        result = closing.compute_round(resumed)
        closing.commit_round(db, resumed, result)
    with factory() as db:
        with pytest.raises(EngineError, match="封轮任务已失效"):
            closing.commit_round(db, first, result)
        assert len(db.scalars(select(RoundResult).where(RoundResult.negotiation_id == nid)).all()) == 1


def test_start_round_solves_after_transaction_commit(api, monkeypatch):
    client, factory = api
    nid, _ = _start(client)
    with factory() as db:
        original = feasibility.run_round_computation
        def inspect_snapshot(snapshot):
            assert not db.in_transaction()
            with factory() as other:
                assert other.get(Negotiation, nid).status == "CHECKING_FEASIBILITY"
            return original(snapshot)
        monkeypatch.setattr(feasibility, "run_round_computation", inspect_snapshot)
        feasibility.start_round(db, db.get(Negotiation, nid))


def test_crashed_feasibility_can_resume_via_close_endpoint(api, monkeypatch):
    client, factory = api
    nid, headers = _start(client)
    with factory() as db:
        negotiation = db.get(Negotiation, nid)
        round_no = negotiation.round_no
        closing.begin_round_close(db, negotiation, db.get(Member, negotiation.owner_member_id), initial=True)
    response = _close(client, nid, headers["a"], round_no)
    assert response.status_code == 200, response.text
    assert response.json()["negotiation_status"] != "CHECKING_FEASIBILITY"


def test_feasibility_input_change_creates_new_job_version(api):
    client, factory = api
    nid, _ = _start(client)
    with factory() as db:
        negotiation = db.get(Negotiation, nid)
        actor = db.get(Member, negotiation.owner_member_id)
        old = closing.begin_round_close(db, negotiation, actor, initial=True)
        negotiation.constraint_version += 1
        negotiation.decision_config_version += 1
        db.flush()
        new = closing.begin_round_close(db, negotiation, actor, initial=True)
        assert new.state_version > old.state_version
        assert new.constraint_version > old.constraint_version


def test_old_rejection_does_not_contaminate_stop(api):
    from app.models.business import ResultConfirmation
    client, factory = api
    nid, headers = _start(client, round_limit=1)
    assert _close(client, nid, headers["a"], 1).status_code == 200
    with factory() as db:
        negotiation = db.get(Negotiation, nid)
        db.add(ResultConfirmation(negotiation_id=nid, member_id=negotiation.owner_member_id,
            result_version=negotiation.active_result_version,
            decision_config_version=negotiation.decision_config_version, accept=False))
        negotiation.decision_config_version += 1
        db.commit()
    response = client.post(f"/negotiations/{nid}/organizer/decision", headers=headers["a"], json={"action": "stop"})
    assert response.status_code == 200, response.text
    assert response.json()["negotiation_status"] == "CLOSED_UNCONFIRMED"


def test_ocr_rejects_anonymous_before_maas(api, monkeypatch):
    from app.services import menu_ocr
    monkeypatch.setattr(menu_ocr, "_call_maas", lambda *args: pytest.fail("MaaS must not be called"))
    client, _ = api
    response = client.post("/menu/parse-image", json={"image_base64": "a" * 20})
    assert response.status_code == 401


def test_image_size_is_checked_before_decoding(monkeypatch):
    from app.services import menu_ocr
    monkeypatch.setattr(settings, "menu_image_max_bytes", 8)
    monkeypatch.setattr(base64, "b64decode", lambda *args, **kwargs: pytest.fail("must not decode"))
    with pytest.raises(EngineError) as error:
        menu_ocr._decode_image("a" * 200, "image/png")
    assert error.value.http_status == 413


def test_ocr_body_limit_before_json_parsing(api, monkeypatch):
    client, _ = api
    response = client.post("/menu/parse-image", content=b"x" * (9 * 1024 * 1024))
    assert response.status_code == 413


def test_ocr_shared_rate_limit_and_dependency_failure(monkeypatch):
    from app.api import menu
    class Redis:
        def eval(self, *args): return settings.menu_ocr_requests_per_minute + 1
    monkeypatch.setattr(menu, "get_client", Redis)
    with pytest.raises(EngineError) as error:
        menu.check_ocr_rate("member:1")
    assert error.value.http_status == 429
    monkeypatch.setattr(menu, "get_client", lambda: (_ for _ in ()).throw(ConnectionError()))
    with pytest.raises(EngineError) as error:
        menu.check_ocr_rate("member:1")
    assert error.value.http_status == 503


def test_solver_budget_exhaustion_skips_auxiliary_solves(api, monkeypatch):
    from app.solver.budget import SolveBudget
    from app.solver import challengers
    client, factory = api
    nid, _ = _start(client)
    with factory() as db:
        negotiation = db.get(Negotiation, nid)
        snapshot = closing.begin_round_close(db, negotiation, db.get(Member, negotiation.owner_member_id))
    actual = closing.solve_mean_fair
    def consumes_budget(mu, model, config):
        return replace(actual(mu, model, config), wall_time=config.time_limit_seconds)
    monkeypatch.setattr(closing, "solve_mean_fair", consumes_budget)
    monkeypatch.setattr(challengers, "solve_mean_fair", lambda *args, **kwargs: pytest.fail("budget exhausted"))
    result = closing.compute_round(snapshot, budget=SolveBudget(0.1))
    assert result.outcome == "feasible"
    assert result.bound_report["bound_source"] == "universal"
    assert not result.challenge_pool["complete"]


def test_unexpected_error_is_structured_and_does_not_expose_parameters(api, monkeypatch, caplog):
    from app.main import app
    from app.api import negotiations
    def fail(*args, **kwargs):
        raise RuntimeError("private-answer-and-secret-token")
    monkeypatch.setattr(negotiations, "create_negotiation", fail)
    from app.tests.test_api_flow import _candidates
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/negotiations", json={"title": "test", "initiator_name": "owner",
        "candidates": _candidates(), "budget_cents": 6000, "exact_count": 2, "round_limit": 2})
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "private-answer-and-secret-token" not in response.text + caplog.text
