from __future__ import annotations

import random
from fractions import Fraction
from itertools import combinations

import pytest

from app.constraints.compiler import ConstraintModel, compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.constraints.validation import precheck, verify_indices, verify_selection
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.dp_baseline import DpUnsupportedError, solve_mean_fair_dp
from app.solver.mean_fair import (
    SolveStatus,
    SolverConfig,
    mean_fair_objective,
)

SEEDS = range(1000)
CATEGORIES = ("main", "side", "drink")


def _public(
    kind: ConstraintKind, constraint_id: str, **payload: object
) -> ConfirmedConstraint:
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


def _config(lam: Fraction) -> SolverConfig:
    return SolverConfig(lam=lam, time_limit_seconds=5.0, num_search_workers=1)


def _build_instance(
    seed: int,
    *,
    n: int = 12,
    d: int = 4,
    k: int = 3,
    with_rows: bool = True,
) -> tuple[ConstraintModel, list[list[float]]]:
    rng = random.Random(seed)
    candidates = [
        Candidate(
            candidate_id=f"c{index:02d}",
            cost_cents=rng.randint(0, 400),
            category=rng.choice(CATEGORIES),
            attributes={"veg": rng.random() < 0.35, "spicy": rng.random() < 0.3},
        )
        for index in range(n)
    ]
    members = [Member(member_id=member) for member in range(1, d + 1)]
    costs = sorted(item.cost_cents for item in candidates)
    budget = sum(costs[:k]) + rng.choice([0, 0, 50, 200, 1000])

    constraints: list[ConfirmedConstraint] = [
        _public(ConstraintKind.BUDGET, "budget", amount_cents=budget),
        _public(ConstraintKind.EXACT_COUNT, "exact", min_count=k),
    ]

    exclude_count = rng.choice([0, 0, 1, 2])
    if exclude_count:
        chosen = rng.sample(candidates, exclude_count)
        constraints.append(
            _public(
                ConstraintKind.EXCLUDE_ITEM,
                "exclude",
                candidate_ids=tuple(item.candidate_id for item in chosen),
            )
        )

    for member in members:
        if rng.random() < 0.25:
            target = rng.choice(candidates)
            constraints.append(
                _member_constraint(
                    ConstraintKind.OPT_OUT,
                    f"opt{member.member_id}",
                    member.member_id,
                    candidate_ids=(target.candidate_id,),
                )
            )

    if with_rows:
        for member in members:
            if rng.random() < 0.4:
                if rng.random() < 0.5:
                    constraints.append(
                        _member_constraint(
                            ConstraintKind.REQUIRE_COUNT,
                            f"req{member.member_id}",
                            member.member_id,
                            category=rng.choice(CATEGORIES),
                            min_count=rng.choice([1, 1, 2]),
                        )
                    )
                else:
                    constraints.append(
                        _member_constraint(
                            ConstraintKind.REQUIRE_COUNT,
                            f"req{member.member_id}",
                            member.member_id,
                            attributes={"veg": True},
                            min_count=1,
                        )
                    )
        if rng.random() < 0.5:
            constraints.append(
                _public(
                    ConstraintKind.CATEGORY_COUNT,
                    "catcap",
                    category=rng.choice(CATEGORIES),
                    min_count=rng.choice([0, 0, 1]),
                    max_count=rng.choice([1, 2]),
                )
            )

    model = compile_constraints(constraints, candidates, members)
    mu = [
        [round(rng.uniform(0.0, 1.0), 6) for _ in range(n)] for _ in range(d)
    ]
    return model, mu


def _enumerate_best(
    model, mu, config: SolverConfig
) -> tuple[float | None, tuple[tuple[int, ...], ...]]:
    best: float | None = None
    combos: list[tuple[int, ...]] = []
    for combo in combinations(range(model.candidate_count), model.exact_count):
        if not verify_indices(model, combo).ok:
            continue
        value = mean_fair_objective(mu, combo, config, exact_count=model.exact_count)
        if best is None or value > best + 1e-12:
            best, combos = value, [combo]
        elif abs(value - best) <= 1e-12:
            combos.append(combo)
    return best, tuple(combos)


@pytest.mark.parametrize("lam", [Fraction(1, 4), Fraction(0, 1)])
def test_cpsat_matches_full_enumeration(lam: Fraction) -> None:
    config = _config(lam)
    quantization_tolerance = 2.0 / config.quantization_scale + 1e-9
    mismatched: list[int] = []

    for seed in SEEDS:
        model, mu = _build_instance(seed)
        best, _ = _enumerate_best(model, mu, config)
        result = solve_mean_fair(mu, model, config)

        if best is None:
            assert result.status in (SolveStatus.INFEASIBLE, SolveStatus.UNKNOWN)
            if result.status is SolveStatus.INFEASIBLE:
                assert "无可行" in result.message
            assert result.candidate_ids == ()
            continue

        if result.status is not SolveStatus.OPTIMAL:
            mismatched.append(seed)
            continue
        assert verify_selection(model, result.candidate_ids).ok
        assert result.tie_break_complete
        assert result.objective_float is not None
        assert result.objective_float <= best + 1e-9
        assert result.objective_float >= best - quantization_tolerance

    assert not mismatched, f"这些 seed 未在时限内证优：{mismatched}"


def test_tie_break_is_lexicographic_and_reproducible() -> None:
    candidates = [Candidate(f"c{index:02d}", 100, "main") for index in range(6)]
    constraints = [
        _public(ConstraintKind.BUDGET, "budget", amount_cents=1000),
        _public(ConstraintKind.EXACT_COUNT, "exact", min_count=2),
    ]
    model = compile_constraints(constraints, candidates, [Member(1), Member(2)])
    mu = [[0.5] * 6 for _ in range(2)]
    config = _config(Fraction(1, 4))

    first = solve_mean_fair(mu, model, config)
    second = solve_mean_fair(mu, model, config)

    assert first.status is SolveStatus.OPTIMAL
    assert first.candidate_ids == ("c00", "c01")
    assert first.tie_break_complete
    assert second.candidate_ids == first.candidate_ids


def test_budget_equality_and_zero_cost_boundaries() -> None:
    candidates = [
        Candidate("c00", 100, "main"),
        Candidate("c01", 100, "main"),
        Candidate("c02", 100, "main"),
    ]
    exact = _public(ConstraintKind.EXACT_COUNT, "exact", min_count=2)
    exact_budget = compile_constraints(
        [_public(ConstraintKind.BUDGET, "budget", amount_cents=200), exact],
        candidates,
        [Member(1)],
    )
    result = solve_mean_fair(
        [[0.1, 0.9, 0.5]], exact_budget, _config(Fraction(1, 4))
    )
    assert result.status is SolveStatus.OPTIMAL
    assert sum(
        exact_budget.costs_cents[exact_budget.candidate_index(item)]
        for item in result.candidate_ids
    ) == 200

    free_candidates = [Candidate(f"c{index:02d}", 0, "main") for index in range(4)]
    free_model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=0),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=2),
        ],
        free_candidates,
        [Member(1)],
    )
    free_result = solve_mean_fair(
        [[0.1, 0.2, 0.8, 0.3]], free_model, _config(Fraction(1, 4))
    )
    assert free_result.status is SolveStatus.OPTIMAL
    assert free_result.candidate_ids == ("c02", "c03")


def test_exclusion_leaving_too_few_candidates_is_proven_infeasible() -> None:
    candidates = [Candidate(f"c{index}", 100, "main") for index in range(4)]
    model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=1000),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=2),
            _public(
                ConstraintKind.EXCLUDE_ITEM,
                "exclude",
                candidate_ids=("c0", "c1", "c2"),
            ),
        ],
        candidates,
        [Member(1)],
    )
    assert precheck(model).definitely_infeasible

    result = solve_mean_fair([[0.5, 0.5, 0.5, 0.5]], model, _config(Fraction(1, 4)))
    assert result.status is SolveStatus.INFEASIBLE
    assert result.candidate_ids == ()


def test_single_requirement_feasible_but_joint_infeasible() -> None:
    candidates = [
        Candidate("c00", 100, "main"),
        Candidate("c01", 100, "side"),
    ]
    model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=1000),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=1),
            _member_constraint(
                ConstraintKind.REQUIRE_COUNT, "req1", 1, category="main", min_count=1
            ),
            _member_constraint(
                ConstraintKind.REQUIRE_COUNT, "req2", 2, category="side", min_count=1
            ),
        ],
        candidates,
        [Member(1), Member(2)],
    )
    assert not precheck(model).definitely_infeasible

    result = solve_mean_fair([[0.5, 0.5], [0.5, 0.5]], model, _config(Fraction(1, 4)))
    assert result.status is SolveStatus.INFEASIBLE


def test_unknown_status_is_not_reported_as_infeasible() -> None:
    rng = random.Random(7)
    candidates = [
        Candidate(f"c{index:02d}", rng.randint(0, 400), rng.choice(CATEGORIES))
        for index in range(30)
    ]
    model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=1200),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=8),
            _public(
                ConstraintKind.CATEGORY_COUNT, "cap", category="main", max_count=2
            ),
        ],
        candidates,
        [Member(member) for member in range(1, 7)],
    )
    mu = [[rng.uniform(0.0, 1.0) for _ in range(30)] for _ in range(6)]
    config = SolverConfig(
        lam=Fraction(1, 4), time_limit_seconds=0.001, num_search_workers=1
    )

    result = solve_mean_fair(mu, model, config)

    assert result.status is not SolveStatus.INFEASIBLE
    assert "无可行" not in result.message
    if result.status is SolveStatus.UNKNOWN:
        assert result.candidate_ids == ()


def test_model_invalid_inputs_are_blocked() -> None:
    candidates = [Candidate("c0", 100, "main"), Candidate("c1", 200, "main")]
    model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=1000),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=1),
        ],
        candidates,
        [Member(1)],
    )
    config = _config(Fraction(1, 4))

    assert solve_mean_fair([[0.5]], model, config).status is SolveStatus.MODEL_INVALID
    assert solve_mean_fair([], model, config).status is SolveStatus.MODEL_INVALID
    assert solve_mean_fair([[2.5, 0.2]], model, config).status is SolveStatus.MODEL_INVALID


def test_dp_matches_enumeration_on_additive_instances() -> None:
    config = _config(Fraction(0, 1))
    for seed in range(40):
        model, mu = _build_instance(seed, with_rows=False)
        best, _ = _enumerate_best(model, mu, config)
        result = solve_mean_fair_dp(mu, model, config)

        if best is None:
            assert result.status is SolveStatus.INFEASIBLE
            continue
        assert result.status is SolveStatus.OPTIMAL
        assert verify_selection(model, result.candidate_ids).ok
        assert result.objective_float is not None
        assert abs(result.objective_float - best) <= 1e-9


def test_dp_refuses_instances_it_cannot_encode() -> None:
    candidates = [
        Candidate("c0", 100, "main"),
        Candidate("c1", 100, "side"),
        Candidate("c2", 100, "side"),
    ]
    model = compile_constraints(
        [
            _public(ConstraintKind.BUDGET, "budget", amount_cents=300),
            _public(ConstraintKind.EXACT_COUNT, "exact", min_count=2),
            _public(ConstraintKind.CATEGORY_COUNT, "cap", category="side", max_count=1),
        ],
        candidates,
        [Member(1)],
    )
    with pytest.raises(DpUnsupportedError):
        solve_mean_fair_dp([[0.5, 0.5, 0.5]], model, _config(Fraction(0, 1)))
    with pytest.raises(DpUnsupportedError):
        solve_mean_fair_dp([[0.5, 0.5, 0.5]], model, _config(Fraction(1, 4)))
