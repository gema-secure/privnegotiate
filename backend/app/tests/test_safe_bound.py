"""P5 保守界：覆盖整个可行域，界宽随覆盖变化。"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations

import pytest

from app.constraints.compiler import compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.constraints.validation import verify_indices
from app.diagnostics.safe_bound import SafeBoundConfig, compute_safe_bound
from app.preference.acceptance import AnswerObservation
from app.preference.bounds import BoundConfig, build_bounds
from app.solver.mean_fair import SolveResult, SolveStatus, SolverConfig, mean_fair_objective


def _model(n: int = 6, d: int = 2, k: int = 2):
    candidates = [Candidate(f"c{index:02d}", 100, "main") for index in range(n)]
    members = [Member(member) for member in range(1, d + 1)]
    return compile_constraints(
        [
            ConfirmedConstraint(
                constraint_id="budget",
                kind=ConstraintKind.BUDGET,
                scope=ConstraintScope.PUBLIC,
                amount_cents=1000,
            ),
            ConfirmedConstraint(
                constraint_id="exact",
                kind=ConstraintKind.EXACT_COUNT,
                scope=ConstraintScope.PUBLIC,
                min_count=k,
            ),
        ],
        candidates,
        members,
    )


def _answers():
    return [
        AnswerObservation(1, ("c00", "c01", "c02"), ("c00",), "c00"),
        AnswerObservation(2, ("c03", "c04"), ("c03",), "c03"),
    ]


def _bounds(model, answers=None):
    return build_bounds(
        answers if answers is not None else _answers(),
        {},
        BoundConfig(w_top=1.0),
        candidate_ids=model.candidate_ids,
        member_ids=model.member_ids,
    )


def _solver(lam: Fraction = Fraction(1, 4)) -> SolverConfig:
    return SolverConfig(lam=lam, time_limit_seconds=2.0, num_search_workers=1)


def test_bound_covers_entire_box_and_never_negative() -> None:
    model = _model()
    bounds = _bounds(model)
    incumbent = ("c00", "c03")
    solver = _solver()
    report = compute_safe_bound(
        bounds, incumbent, model, solver, SafeBoundConfig(time_limit_seconds=2.0)
    )

    assert 0.0 <= report.epsilon_safe <= 1.0
    assert report.bound_source == "solver"
    assert report.unknown_pairs == 7
    assert 0.0 < report.coverage_ratio < 1.0

    upper_best = None
    for combo in combinations(range(model.candidate_count), model.exact_count):
        if not verify_indices(model, combo).ok:
            continue
        value = mean_fair_objective(bounds.upper, combo, solver, exact_count=model.exact_count)
        upper_best = value if upper_best is None else max(upper_best, value)
    assert upper_best is not None
    assert report.epsilon_safe + 1e-9 >= upper_best - report.lower_bound_incumbent


def test_more_labels_do_not_widen_the_bound() -> None:
    model = _model()
    empty = build_bounds(
        [], {}, candidate_ids=model.candidate_ids, member_ids=model.member_ids
    )
    before = compute_safe_bound(
        empty, ("c00", "c03"), model, _solver(), SafeBoundConfig(time_limit_seconds=2.0)
    )
    after = compute_safe_bound(
        _bounds(model),
        ("c00", "c03"),
        model,
        _solver(),
        SafeBoundConfig(time_limit_seconds=2.0),
    )
    assert after.epsilon_safe <= before.epsilon_safe + 1e-9
    assert after.coverage_ratio > before.coverage_ratio


def test_solver_unknown_falls_back_to_universal_bound(monkeypatch) -> None:
    model = _model()
    bounds = _bounds(model)

    def fake_solver(*args, **kwargs):
        _ = args, kwargs
        return SolveResult(status=SolveStatus.UNKNOWN, message="timeout")

    monkeypatch.setattr("app.diagnostics.safe_bound.solve_mean_fair", fake_solver)
    report = compute_safe_bound(
        bounds, ("c00", "c03"), model, _solver(), SafeBoundConfig(time_limit_seconds=2.0)
    )
    assert report.bound_source == "universal"
    assert report.upper_bound_h == 1.0
    assert report.epsilon_safe == pytest.approx(1.0 - report.lower_bound_incumbent)


def test_infeasible_incumbent_returns_generic_bound() -> None:
    model = _model()
    report = compute_safe_bound(
        _bounds(model), ("c00",), model, _solver(), SafeBoundConfig(time_limit_seconds=2.0)
    )
    assert report.epsilon_safe == 1.0
    assert "硬约束" in report.message


def test_upper_bound_from_pool_is_used_without_new_solve(monkeypatch) -> None:
    model = _model()

    def fail(*args, **kwargs):
        raise AssertionError("不应重复求解")

    monkeypatch.setattr("app.diagnostics.safe_bound.solve_mean_fair", fail)
    incumbent = ("c00", "c03")
    solver = _solver()
    lower = compute_safe_bound(
        _bounds(model),
        incumbent,
        model,
        solver,
        SafeBoundConfig(time_limit_seconds=2.0),
        upper_bound=0.75,
        upper_status="OPTIMAL",
        upper_quantization_bound=0.001,
    )
    assert lower.bound_source == "solver"
    assert lower.upper_bound_h == 0.75
