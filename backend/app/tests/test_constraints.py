from __future__ import annotations

import pytest

from app.constraints.compiler import CompileError, compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    DslError,
    Member,
    parse_constraint,
)
from app.constraints.validation import precheck, verify_selection

BUDGET = ConstraintKind.BUDGET
EXACT = ConstraintKind.EXACT_COUNT
EXCLUDE = ConstraintKind.EXCLUDE_ITEM
OPT_OUT = ConstraintKind.OPT_OUT
REQUIRE = ConstraintKind.REQUIRE_COUNT
CATEGORY = ConstraintKind.CATEGORY_COUNT


def _candidate(
    index: int,
    cost: int,
    category: str = "main",
    **attributes: object,
) -> Candidate:
    return Candidate(
        candidate_id=f"c{index}",
        cost_cents=cost,
        category=category,
        attributes=attributes,
    )


def _members(count: int = 2) -> tuple[Member, ...]:
    return tuple(Member(member_id=member) for member in range(1, count + 1))


def _public(kind: ConstraintKind, constraint_id: str, **payload: object) -> ConfirmedConstraint:
    return ConfirmedConstraint(
        constraint_id=constraint_id,
        kind=kind,
        scope=ConstraintScope.PUBLIC,
        **payload,
    )


def _member_constraint(
    kind: ConstraintKind, constraint_id: str, owner: int, **payload: object
) -> ConfirmedConstraint:
    return ConfirmedConstraint(
        constraint_id=constraint_id,
        kind=kind,
        scope=ConstraintScope.MEMBER,
        owner_member_id=owner,
        **payload,
    )


def _base(budget: int = 2000, exact: int = 2) -> list[ConfirmedConstraint]:
    return [
        _public(BUDGET, "budget", amount_cents=budget),
        _public(EXACT, "exact", min_count=exact),
    ]


def test_dsl_rejects_unknown_kind_and_expression_fields() -> None:
    with pytest.raises(DslError):
        parse_constraint({"constraint_id": "x", "kind": "eval", "expression": "1+1"})

    with pytest.raises(DslError):
        parse_constraint(
            {"constraint_id": "x", "kind": "budget", "amount_cents": 10, "expression": "1"}
        )


def test_dsl_rejects_scope_that_does_not_match_kind() -> None:
    with pytest.raises(DslError):
        parse_constraint(
            {"constraint_id": "x", "kind": "budget", "amount_cents": 10, "scope": "member"}
        )


def test_dsl_require_count_needs_condition_and_owner() -> None:
    with pytest.raises(DslError):
        parse_constraint(
            {"constraint_id": "r1", "kind": "require_count", "min_count": 1}
        )

    with pytest.raises(DslError):
        parse_constraint(
            {
                "constraint_id": "r1",
                "kind": "require_count",
                "owner_member_id": 1,
                "min_count": 1,
                "candidate_ids": ["c1"],
            }
        )


def test_dsl_attributes_only_accept_scalar_values() -> None:
    with pytest.raises(DslError):
        parse_constraint(
            {
                "constraint_id": "r1",
                "kind": "require_count",
                "owner_member_id": 1,
                "min_count": 1,
                "attributes": {"tags": ["veg"]},
            }
        )


def test_compile_marks_opt_out_and_keeps_requirement_unsatisfied() -> None:
    candidates = [
        _candidate(1, 100, "main", vegetarian=False),
        _candidate(2, 200, "side", vegetarian=True),
        _candidate(3, 300, "side", vegetarian=True),
        _candidate(4, 400, "drink"),
        _candidate(5, 500, "main", vegetarian=True),
    ]
    constraints = _base() + [
        _member_constraint(OPT_OUT, "o1", 1, candidate_ids=("c3",)),
        _member_constraint(REQUIRE, "r1", 1, attributes={"vegetarian": True}, min_count=1),
    ]

    model = compile_constraints(constraints, candidates, _members())

    assert model.e[0][2] == 0
    assert model.e[1][2] == 1
    vegetarian_row = next(row for row in model.rows if row.label == "require:r1")
    assert vegetarian_row.coefficients == (0, 1, 0, 0, 1)
    assert vegetarian_row.lower == 1


def test_compile_attributes_must_match_type_and_value() -> None:
    candidates = [
        _candidate(1, 100, vegetarian=1),
        _candidate(2, 200, vegetarian=True),
    ]
    constraints = _base() + [
        _member_constraint(REQUIRE, "r1", 1, attributes={"vegetarian": True}, min_count=1)
    ]

    model = compile_constraints(constraints, candidates, _members())

    row = next(row for row in model.rows if row.label == "require:r1")
    assert row.coefficients == (0, 1)


def test_compile_builds_exclusion_and_category_rows() -> None:
    candidates = [
        _candidate(1, 100, "main"),
        _candidate(2, 200, "side"),
        _candidate(3, 300, "side"),
        _candidate(4, 400, "drink"),
        _candidate(5, 500, "main"),
    ]
    constraints = _base() + [
        _public(EXCLUDE, "e1", candidate_ids=("c4",)),
        _public(CATEGORY, "cat1", category="side", min_count=1, max_count=1),
    ]

    model = compile_constraints(constraints, candidates, _members())

    assert model.excluded_indices == frozenset({3})
    category_row = next(row for row in model.rows if row.label == "category:side:cat1")
    assert category_row.coefficients == (0, 1, 1, 0, 0)
    assert (category_row.lower, category_row.upper) == (1, 1)


def test_compile_rejects_unknown_references_and_missing_public_conditions() -> None:
    candidates = [_candidate(1, 100), _candidate(2, 200), _candidate(3, 300)]

    with pytest.raises(CompileError):
        compile_constraints(
            _base() + [_public(EXCLUDE, "e1", candidate_ids=("c9",))],
            candidates,
            _members(),
        )
    with pytest.raises(CompileError):
        compile_constraints(
            _base() + [_member_constraint(OPT_OUT, "o1", 9, candidate_ids=("c1",))],
            candidates,
            _members(),
        )
    with pytest.raises(CompileError):
        compile_constraints([_public(BUDGET, "budget", amount_cents=100)], candidates, _members())


def test_precheck_flags_definite_infeasibility_but_not_feasibility() -> None:
    candidates = [_candidate(index, 100 * index) for index in range(1, 6)]
    constraints = _base(budget=150, exact=2) + [
        _public(EXCLUDE, "e1", candidate_ids=("c1", "c2", "c3", "c4"))
    ]
    model = compile_constraints(constraints, candidates, _members())
    report = precheck(model)
    assert report.definitely_infeasible
    assert any(issue.code == "NOT_ENOUGH_ELIGIBLE" for issue in report.issues)

    ok_model = compile_constraints(_base(budget=2000, exact=2), candidates, _members())
    ok_report = precheck(ok_model)
    assert not ok_report.definitely_infeasible
    assert ok_report.issues == ()


def test_precheck_budget_and_requirement_bounds() -> None:
    candidates = [_candidate(index, 1000) for index in range(1, 6)]
    tight = compile_constraints(_base(budget=1999, exact=2), candidates, _members())
    assert any(issue.code == "BUDGET_TOO_LOW" for issue in precheck(tight).issues)

    requirement = compile_constraints(
        _base(budget=5000, exact=2)
        + [_member_constraint(REQUIRE, "r1", 1, category="drink", min_count=2)],
        candidates,
        _members(),
    )
    assert any(
        issue.code == "ROW_LOWER_IMPOSSIBLE" for issue in precheck(requirement).issues
    )


def test_precheck_cannot_detect_joint_infeasibility() -> None:
    candidates = [
        _candidate(1, 100, "main"),
        _candidate(2, 100, "side"),
    ]
    constraints = _base(budget=1000, exact=1) + [
        _member_constraint(REQUIRE, "r1", 1, category="main", min_count=1),
        _member_constraint(REQUIRE, "r2", 2, category="side", min_count=1),
    ]
    model = compile_constraints(constraints, candidates, _members())

    report = precheck(model)
    assert not report.definitely_infeasible
    assert report.issues == ()


def test_verify_selection_rejects_each_constraint_violation() -> None:
    candidates = [
        _candidate(1, 100, "main"),
        _candidate(2, 200, "side"),
        _candidate(3, 300, "side"),
        _candidate(4, 400, "drink"),
    ]
    constraints = _base(budget=350, exact=2) + [
        _public(EXCLUDE, "e1", candidate_ids=("c4",)),
        _public(CATEGORY, "cat1", category="side", min_count=1),
        _member_constraint(REQUIRE, "r1", 1, category="side", min_count=1),
    ]
    model = compile_constraints(constraints, candidates, _members())

    assert not verify_selection(model, ["c1"]).ok
    assert not verify_selection(model, ["c3", "c4"]).ok
    assert not verify_selection(model, ["c2", "c3", "c1"]).ok
    assert not verify_selection(model, ["c9"]).ok
    assert not verify_selection(model, ["c1", "c1"]).ok
    assert not verify_selection(model, ["c2", "c3"]).ok

    valid = verify_selection(model, ["c1", "c2"])
    assert valid.ok
    assert valid.total_cost_cents == 300
    assert valid.violations == ()


def test_precheck_issue_definite_flag_gates_infeasibility() -> None:
    """非 definite 的 issue 只作告警，不得被判成确定无解。"""
    from app.constraints.validation import PrecheckIssue, PrecheckReport

    blocking = PrecheckReport(
        issues=(PrecheckIssue("NO_ELIGIBLE_CANDIDATE", "整组禁选后没有候选"),),
        definitely_infeasible=True,
    )
    assert all(issue.definite for issue in blocking.issues)

    advisory = PrecheckIssue("SPREAD_LOW", "类别覆盖偏低，建议补充", definite=False)
    assert advisory.definite is False
    assert not any(issue.definite for issue in (advisory,))
