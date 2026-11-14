"""三态属性层：未知不排除、不放行、不冒充无解，且人工修正留痕。"""

from __future__ import annotations

from fractions import Fraction

from sqlalchemy import select

from app.constraints.compiler import compile_constraints, pending_for_requirement
from app.constraints.dsl import (
    AttributeState,
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.constraints.validation import precheck
from app.models.business import CandidateAttributeRevision
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolveStatus, SolverConfig

BUDGET = ConstraintKind.BUDGET
EXACT = ConstraintKind.EXACT_COUNT
EXCLUDE = ConstraintKind.EXCLUDE_ITEM
OPT_OUT = ConstraintKind.OPT_OUT
REQUIRE = ConstraintKind.REQUIRE_COUNT


def _candidate(index: int, peanut: str | None, *, cost: int = 100) -> Candidate:
    states = {} if peanut is None else {"peanut": AttributeState(peanut)}
    return Candidate(
        candidate_id=f"c{index}", cost_cents=cost, category="main", attribute_states=states
    )


def _members(count: int = 2) -> tuple[Member, ...]:
    return tuple(Member(member_id=member) for member in range(1, count + 1))


def _base() -> list[ConfirmedConstraint]:
    return [
        ConfirmedConstraint(
            constraint_id="budget",
            kind=BUDGET,
            scope=ConstraintScope.PUBLIC,
            amount_cents=2000,
        ),
        ConfirmedConstraint(
            constraint_id="exact",
            kind=EXACT,
            scope=ConstraintScope.PUBLIC,
            min_count=2,
        ),
    ]


def _require(
    owner: int, constraint_id: str = "r1", *, min_count: int = 1, peanut: bool = False
) -> ConfirmedConstraint:
    return ConfirmedConstraint(
        constraint_id=constraint_id,
        kind=REQUIRE,
        scope=ConstraintScope.MEMBER,
        owner_member_id=owner,
        attributes={"peanut": peanut},
        min_count=min_count,
    )


def test_unknown_attribute_enters_pending_not_excluded() -> None:
    candidates = [_candidate(1, "unknown"), _candidate(2, "unknown")]
    model = compile_constraints(
        _base() + [_require(1)], candidates, _members()
    )

    row = next(item for item in model.rows if item.label == "require:r1")
    assert row.coefficients == (1, 1)
    assert row.uncertain_indices == (0, 1)
    assert len(model.pending_checks) == 1
    check = model.pending_checks[0]
    assert check.constraint_id == "r1"
    assert check.confirmed_count == 0
    assert check.unconfirmed_candidate_ids == ("c1", "c2")

    report = precheck(model)
    assert report.definitely_infeasible is False
    assert "ATTRIBUTE_UNKNOWN_PENDING" in [issue.code for issue in report.issues]

    result = solve_mean_fair(
        [[0.5, 0.5], [0.5, 0.5]],
        model,
        SolverConfig(lam=Fraction(1, 4), time_limit_seconds=5.0, num_search_workers=1),
    )
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


def test_known_absent_matches_but_unknown_is_not_absent() -> None:
    candidates = [
        _candidate(1, "absent"),
        _candidate(2, "unknown"),
        _candidate(3, "present"),
    ]
    model = compile_constraints(
        _base() + [_require(1, min_count=2)], candidates, _members()
    )

    row = next(item for item in model.rows if item.label == "require:r1")
    assert row.coefficients == (1, 1, 0)
    assert row.uncertain_indices == (1,)
    check = model.pending_checks[0]
    assert check.confirmed_count == 1
    assert check.unconfirmed_candidate_ids == ("c2",)
    assert pending_for_requirement(
        _require(1, min_count=1), candidates, eligible_ids=["c1", "c2", "c3"]
    ) is None


def test_group_exclusion_and_member_bottomline_coexist() -> None:
    candidates = [
        _candidate(1, "absent"),
        _candidate(2, "absent"),
        _candidate(3, "absent"),
    ]
    constraints = _base() + [
        ConfirmedConstraint(
            constraint_id="ban",
            kind=EXCLUDE,
            scope=ConstraintScope.PUBLIC,
            candidate_ids=("c1",),
        ),
        ConfirmedConstraint(
            constraint_id="mine",
            kind=OPT_OUT,
            scope=ConstraintScope.MEMBER,
            owner_member_id=1,
            candidate_ids=("c2",),
        ),
        _require(1, "r1"),
        _require(2, "r2"),
    ]
    model = compile_constraints(constraints, candidates, _members())

    assert model.excluded_indices == frozenset({0})
    assert model.e[0][1] == 0
    assert model.e[1][1] == 1
    row_one = next(item for item in model.rows if item.label == "require:r1")
    row_two = next(item for item in model.rows if item.label == "require:r2")
    assert row_one.coefficients == (1, 0, 1)
    assert row_two.coefficients == (1, 1, 1)

    result = solve_mean_fair(
        [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5]],
        model,
        SolverConfig(lam=Fraction(1, 4), time_limit_seconds=5.0, num_search_workers=1),
    )
    assert "c1" not in result.candidate_ids
    assert set(result.candidate_ids) == {"c2", "c3"}


def test_unknown_does_not_fake_infeasibility_but_real_shortage_does() -> None:
    unknown = [_candidate(1, "unknown"), _candidate(2, "unknown")]
    model = compile_constraints(
        _base() + [_require(1, min_count=2)], unknown, _members()
    )
    assert precheck(model).definitely_infeasible is False

    shortage = [_candidate(1, "absent"), _candidate(2, "present")]
    impossible = compile_constraints(
        _base() + [_require(1, min_count=2)], shortage, _members()
    )
    assert impossible.pending_checks == ()
    report = precheck(impossible)
    assert report.definitely_infeasible is True
    assert "ROW_LOWER_IMPOSSIBLE" in [issue.code for issue in report.issues]


def _candidates_payload() -> list[dict]:
    states = ["unknown"] * 13
    states[0] = "absent"
    states[1] = "absent"
    return [
        {
            "candidate_id": f"c{index:02d}",
            "name": f"菜品{index}",
            "cost_cents": 800 + index * 50,
            "category": "main",
            "confirmed_attributes": {},
            "attributes": [{"key": "peanut", "state": states[index - 1], "source": "import"}],
        }
        for index in range(1, 14)
    ]


def _create(client, member_count: int = 3):
    return client.post(
        "/negotiations",
        json={
            "title": "周五聚餐",
            "initiator_name": "组长",
            "member_count": member_count,
            "candidates": _candidates_payload(),
            "budget_cents": 6000,
            "exact_count": 2,
            "round_limit": 2,
        },
    )


def test_api_persists_tristate_and_correction_clears_pending(api) -> None:
    client, factory = api
    created = _create(client)
    assert created.status_code == 201, created.text
    body = created.json()
    negotiation_id = body["negotiation_id"]
    owner_headers = {"Authorization": f"Bearer {body['member_token']}"}

    joined = client.post(
        f"/negotiations/{negotiation_id}/members",
        json={"invite_token": body["invite_token"], "display_name": "小林"},
    )
    assert joined.status_code == 201, joined.text
    member_headers = {"Authorization": f"Bearer {joined.json()['member_token']}"}

    for headers in (owner_headers, member_headers):
        response = client.post(
            f"/negotiations/{negotiation_id}/me/constraints",
            json={
                "constraints": [
                    {
                        "kind": "require_count",
                        "attributes": {"peanut": False},
                        "min_count": 3,
                    }
                ],
                "no_extra_bottomline": False,
                "expected_preference_version": 1,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text

    public = client.get(f"/negotiations/{negotiation_id}/public")
    assert public.status_code == 200
    first = public.json()["candidates"][0]
    assert first["attributes"] == [
        {"key": "peanut", "state": "absent", "source": "import"}
    ]

    me = client.get(f"/negotiations/{negotiation_id}/me", headers=owner_headers)
    assert me.status_code == 200, me.text
    pending = me.json()["pending_attribute_checks"]
    assert len(pending) == 1
    assert pending[0]["required_count"] == 3
    assert pending[0]["confirmed_count"] == 2
    assert "c03" in pending[0]["unconfirmed_candidate_ids"]

    denied = client.post(
        f"/negotiations/{negotiation_id}/candidates/c03/attributes",
        json={"attributes": [{"key": "peanut", "state": "absent"}]},
        headers=member_headers,
    )
    assert denied.status_code == 403

    corrected = client.post(
        f"/negotiations/{negotiation_id}/candidates/c03/attributes",
        json={
            "attributes": [{"key": "peanut", "state": "absent", "source_detail": "商家确认"}],
            "reason": "电话向商家确认不含花生",
        },
        headers=owner_headers,
    )
    assert corrected.status_code == 200, corrected.text
    corrected_item = next(
        item
        for item in corrected.json()["candidates"]
        if item["candidate_id"] == "c03"
    )
    assert corrected_item["attributes"] == [
        {"key": "peanut", "state": "absent", "source": "correction"}
    ]

    session = factory()
    try:
        revisions = session.scalars(select(CandidateAttributeRevision)).all()
    finally:
        session.close()
    assert len(revisions) == 1
    assert revisions[0].attribute_key == "peanut"
    assert revisions[0].previous_state == "unknown"
    assert revisions[0].new_state == "absent"
    assert revisions[0].reason == "电话向商家确认不含花生"

    me_after = client.get(f"/negotiations/{negotiation_id}/me", headers=owner_headers)
    assert me_after.json()["pending_attribute_checks"] == []


def test_member_count_requires_at_least_three(api) -> None:
    client, _ = api
    assert _create(client, member_count=2).status_code == 422
    assert _create(client, member_count=16).status_code == 422
    assert _create(client, member_count=15).status_code == 201
