"""P5 挑战池：结构、去重、上限与可行性。"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from app.constraints.compiler import compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.constraints.validation import verify_selection
from app.preference.bounds import UtilityBounds
from app.solver.challengers import ChallengeConfig, build_challengers
from app.solver.mean_fair import SolverConfig

CATEGORIES = ("main", "side", "drink")


def _public(kind: ConstraintKind, constraint_id: str, **payload: object) -> ConfirmedConstraint:
    return ConfirmedConstraint(
        constraint_id=constraint_id, kind=kind, scope=ConstraintScope.PUBLIC, **payload
    )


def _model(n: int = 8, d: int = 3, k: int = 2):
    candidates = [
        Candidate(f"c{index:02d}", 100, CATEGORIES[index % 3]) for index in range(n)
    ]
    members = [Member(member) for member in range(1, d + 1)]
    return compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=1000),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=k),
        ],
        candidates,
        members,
    )


def _bounds(model, upper=None) -> UtilityBounds:
    rows = tuple(tuple(2.0 for _ in model.candidate_ids) for _ in model.member_ids)
    if upper is None:
        upper = rows
    return UtilityBounds(
        member_ids=tuple(model.member_ids),
        candidate_ids=tuple(model.candidate_ids),
        lower=tuple(tuple(0.0 for _ in model.candidate_ids) for _ in model.member_ids),
        upper=upper,
    )


def _config(**overrides) -> SolverConfig:
    return SolverConfig(
        lam=Fraction(1, 4), time_limit_seconds=1.0, num_search_workers=1, **overrides
    )


def test_pool_contains_current_personal_and_at_least_three_challengers() -> None:
    model = _model()
    mu = [
        [0.9, 0.7, 0.2, 0.3, 0.8, 0.1, 0.4, 0.6],
        [0.8, 0.6, 0.9, 0.1, 0.2, 0.3, 0.7, 0.5],
        [0.7, 0.8, 0.6, 0.9, 0.1, 0.2, 0.3, 0.4],
    ]
    incumbent = ("c00", "c01")
    pool = build_challengers(
        mu,
        _bounds(model),
        model,
        incumbent,
        _config(),
        challenge_config=ChallengeConfig(time_limit_seconds=1.0),
    )

    assert pool.incumbent_ids == incumbent
    assert pool.challengers[0].role == "incumbent"
    roles = [item.role for item in pool.challengers]
    assert roles.count("personal") == 3
    distinct = {
        frozenset(item.candidate_ids) for item in pool.challengers if item.role != "incumbent"
    }
    assert len(distinct) >= 3
    assert all(verify_selection(model, item.candidate_ids).ok for item in pool.challengers)
    assert pool.upper_bound is not None


def test_personal_challenger_maximizes_that_members_utility() -> None:
    model = _model()
    mu = [
        [0.9, 0.8, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        [0.1, 0.2, 0.9, 0.8, 0.3, 0.4, 0.5, 0.6],
        [0.1, 0.2, 0.3, 0.4, 0.9, 0.8, 0.5, 0.6],
    ]
    pool = build_challengers(
        mu, _bounds(model), model, ("c06", "c07"), _config()
    )
    personal = [item for item in pool.challengers if item.role == "personal"]
    assert {item.member_id for item in personal} == {1, 2, 3}
    expected = {
        1: ("c00", "c01"),
        2: ("c02", "c03"),
        3: ("c04", "c05"),
    }
    for item in personal:
        assert item.candidate_ids == expected[item.member_id]


def test_previous_result_is_included_once() -> None:
    model = _model()
    mu = [[0.5] * 8 for _ in range(3)]
    pool = build_challengers(
        mu,
        _bounds(model),
        model,
        ("c00", "c01"),
        _config(),
        previous_incumbent=("c02", "c03"),
    )
    previous = [item for item in pool.challengers if item.role == "previous"]
    assert len(previous) == 1
    assert previous[0].candidate_ids == ("c02", "c03")
    keys = [frozenset(item.candidate_ids) for item in pool.challengers]
    assert len(keys) == len(set(keys))


def test_pool_respects_max_size() -> None:
    model = _model(30, 6, 8)
    mu = [[0.5] * 30 for _ in range(6)]
    pool = build_challengers(
        mu,
        _bounds(model),
        model,
        ("c00", "c01", "c02", "c03", "c04", "c05", "c06", "c07"),
        _config(),
        challenge_config=ChallengeConfig(max_pool=5, time_limit_seconds=0.5),
    )
    assert len(pool.challengers) <= 5


def test_alignment_mismatch_is_rejected() -> None:
    model = _model()
    bounds = replace(_bounds(model), candidate_ids=tuple(reversed(model.candidate_ids)))
    with pytest.raises(ValueError):
        build_challengers([[0.5] * 8] * 3, bounds, model, ("c00", "c01"), _config())


def test_personal_challenger_is_skipped_when_member_has_no_signal() -> None:
    """mu 全 0 的成员下 λ=0 目标恒为 0，求解器返回任意解，不能当个人方向入池。"""
    model = _model()
    mu = [
        [0.9, 0.7, 0.2, 0.3, 0.8, 0.1, 0.4, 0.6],
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [0.7, 0.8, 0.6, 0.9, 0.1, 0.2, 0.3, 0.4],
    ]
    pool = build_challengers(
        mu,
        _bounds(model),
        model,
        ("c00", "c01"),
        _config(),
        challenge_config=ChallengeConfig(time_limit_seconds=1.0),
    )
    personal = [item for item in pool.challengers if item.role == "personal"]
    assert [item.member_id for item in personal] == [1, 3]
    assert pool.personal_skipped == 1
    assert "无正效用信号" in pool.message
    assert pool.as_dict()["personal_skipped"] == 1


def test_personal_rows_are_not_shared_mutable_state() -> None:
    """个人方向的其他成员行必须是独立零行，不能共享同一 list 对象。"""
    model = _model(d=4)
    mu = [
        [0.9, 0.7, 0.2, 0.3, 0.8, 0.1, 0.4, 0.6],
        [0.8, 0.6, 0.9, 0.1, 0.2, 0.3, 0.7, 0.5],
        [0.7, 0.8, 0.6, 0.9, 0.1, 0.2, 0.3, 0.4],
        [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.9, 0.8],
    ]
    snapshot = [list(row) for row in mu]
    pool = build_challengers(
        mu,
        _bounds(model),
        model,
        ("c00", "c01"),
        _config(),
        challenge_config=ChallengeConfig(time_limit_seconds=1.0),
    )
    assert mu == snapshot  # 入参未被改写
    assert len([item for item in pool.challengers if item.role == "personal"]) == 4
