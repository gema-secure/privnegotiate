from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
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
from app.preference.acceptance import (
    AcceptancePrior,
    AnswerObservation,
    rebuild_acceptance,
)
from app.preference.bounds import BoundConfig, build_bounds
from app.preference.estimates import UtilityConfig, estimate_utilities
from app.preference.relative_choice import (
    NumericConfig,
    RelativeChoicePrior,
    fit_relative_choice,
    pl_gradient,
    pl_hessian,
    pl_objective,
)
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolverConfig

CATEGORIES = {"c1": "main", "c2": "main", "c3": "side", "c4": "side", "c5": "main"}


def _answer(member: int, evaluated: tuple[str, ...], acceptable: tuple[str, ...], top: str | None = None) -> AnswerObservation:
    return AnswerObservation(
        member_id=member,
        evaluated_ids=evaluated,
        acceptable_ids=acceptable,
        top_id=top,
    )


def test_beta_posterior_counts_distinct_candidates_and_latest_labels() -> None:
    answers = [
        _answer(1, ("c1", "c2"), ("c1",), "c1"),
        _answer(1, ("c1", "c2"), ("c1",), "c1"),
        AnswerObservation(member_id=1, mode="skip"),
        _answer(1, ("c1",), ()),
    ]
    state = rebuild_acceptance(answers, CATEGORIES)

    assert state.labels[(1, "c1")] is False
    assert state.labels[(1, "c2")] is False
    main = state.category_state(1, "main")
    assert (main.n, main.s) == (2, 0)
    assert main.p_hat == pytest.approx(0.25)
    assert state.p_and_variance(1, "c1") == (0.0, 0.0)
    assert state.p_and_variance(1, "c3") == (0.5, 0.25)


def test_beta_posterior_formula_matches_document() -> None:
    state = rebuild_acceptance([_answer(1, ("c1",), ("c1",), "c1")], CATEGORIES)
    main = state.category_state(1, "main")

    alpha, beta = 2.0, 1.0
    assert main.alpha == alpha and main.beta == beta
    assert main.p_hat == pytest.approx(2 / 3)
    assert main.var_p == pytest.approx(alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1)))
    assert main.var_p == pytest.approx(1 / 18)


def test_prior_configuration_controls_empty_category() -> None:
    state = rebuild_acceptance([], CATEGORIES, AcceptancePrior(p0=0.3, kappa=4.0))
    assert state.member_ids == ()
    empty = state.category_state(7, "main")
    assert empty.p_hat == pytest.approx(0.3)
    assert empty.alpha == pytest.approx(1.2)
    assert empty.beta == pytest.approx(2.8)


def test_unknown_candidate_in_answer_is_rejected() -> None:
    with pytest.raises(ValueError):
        rebuild_acceptance([_answer(1, ("c9",), ("c9",), "c9")], CATEGORIES)


def test_answer_observation_validates_protocol() -> None:
    with pytest.raises(ValueError):
        _answer(1, ("c1", "c2"), ("c3",), "c3")
    with pytest.raises(ValueError):
        _answer(1, ("c1",), (), "c1")
    with pytest.raises(ValueError):
        AnswerObservation(member_id=1, mode="skip", evaluated_ids=("c1",))


def test_pl_gradient_and_hessian_match_finite_differences() -> None:
    choices = (((0, 1, 2), 0),)
    vector = np.array([0.2, -0.1, 0.3])
    sigma_v = 1.0

    gradient = pl_gradient(vector, choices, sigma_v)
    numeric_gradient = np.zeros(3)
    step = 1e-6
    for index in range(3):
        forward = vector.copy()
        backward = vector.copy()
        forward[index] += step
        backward[index] -= step
        numeric_gradient[index] = (
            pl_objective(forward, choices, sigma_v)
            - pl_objective(backward, choices, sigma_v)
        ) / (2 * step)
    assert np.allclose(gradient, numeric_gradient, atol=1e-6)

    hessian = pl_hessian(vector, choices, sigma_v)
    numeric_hessian = np.zeros((3, 3))
    for index in range(3):
        forward = vector.copy()
        backward = vector.copy()
        forward[index] += step
        backward[index] -= step
        numeric_hessian[:, index] = (
            pl_gradient(forward, choices, sigma_v)
            - pl_gradient(backward, choices, sigma_v)
        ) / (2 * step)
    assert np.allclose(hessian, numeric_hessian, atol=1e-5)


def test_pl_fit_orders_top_item_and_produces_covariance() -> None:
    answers = [_answer(1, ("c1", "c2"), ("c1", "c2"), "c1")]
    state = fit_relative_choice(answers)
    member = state.member_state(1)

    assert member is not None and member.converged
    assert member.used_fallback is False
    assert member.gradient_norm is not None and member.gradient_norm <= 1e-6
    mean_top, var_top = state.posterior(1, "c1")
    mean_other, var_other = state.posterior(1, "c2")
    assert mean_top > 0 > mean_other
    assert 0 < var_top <= 1.0
    assert 0 < var_other <= 1.0


def test_single_or_empty_acceptable_set_adds_no_relative_evidence() -> None:
    base_answers = [_answer(1, ("c1", "c2"), ("c1", "c2"), "c1")]
    base = fit_relative_choice(base_answers)
    with_single = fit_relative_choice(
        base_answers
        + [
            _answer(1, ("c1",), ("c1",), "c1"),
            _answer(1, ("c2",), ()),
        ]
    )

    assert with_single.member_state(1).means == base.member_state(1).means
    assert with_single.member_state(1).variances == base.member_state(1).variances

    only_single = fit_relative_choice([_answer(1, ("c1",), ("c1",), "c1")])
    assert only_single.member_state(1).status == "no_effective_answers"
    assert only_single.posterior(1, "c1") == (0.0, 1.0)


def test_pl_fallback_on_iteration_limit_and_unseen_candidate() -> None:
    numeric = NumericConfig(max_iterations=1)
    state = fit_relative_choice(
        [_answer(1, ("c1", "c2", "c3"), ("c1", "c2", "c3"), "c1")], numeric_config=numeric
    )
    member = state.member_state(1)

    assert member is not None
    assert member.converged is False and member.used_fallback is True
    assert member.status == "max_iterations"
    assert state.posterior(1, "c1") == (0.0, 1.0)

    fitted = fit_relative_choice([_answer(2, ("c1", "c2"), ("c1", "c2"), "c1")])
    assert fitted.posterior(2, "c5") == (0.0, 1.0)


def test_estimate_utilities_combines_labels_opt_out_and_prior() -> None:
    answers = [_answer(1, ("c1", "c2", "c3"), ("c1", "c3"), "c1")]
    acceptance = rebuild_acceptance(answers, CATEGORIES)
    relative = fit_relative_choice(answers)
    estimate = estimate_utilities(
        acceptance,
        relative,
        {1: ("c4",)},
        UtilityConfig(w_top=1.0),
        seed=3,
    )
    row = dict(zip(estimate.candidate_ids, estimate.mu[0]))
    scales = dict(zip(estimate.candidate_ids, estimate.scale[0]))

    assert row["c2"] == 0.0 and scales["c2"] == 0.0
    assert row["c4"] == 0.0 and scales["c4"] == 0.0
    assert row["c1"] > 1.5 > row["c3"] > 1.0
    assert row["c5"] == pytest.approx(0.75, abs=1e-9)
    assert all(0.0 <= value <= 2.0 for value in row.values())
    assert all(0.0 <= value <= 1.0 for value in scales.values())
    assert scales["c3"] > 0.0


def test_estimate_is_deterministic_and_seed_independent() -> None:
    answers = [_answer(1, ("c1", "c2"), ("c1", "c2"), "c1")]
    acceptance = rebuild_acceptance(answers, CATEGORIES)
    relative = fit_relative_choice(answers)

    first = estimate_utilities(acceptance, relative, {}, UtilityConfig(), seed=1)
    second = estimate_utilities(acceptance, relative, {}, UtilityConfig(), seed=99)

    assert first.mu == second.mu
    assert first.scale == second.scale


def test_bounds_are_independent_and_follow_the_table() -> None:
    answers = [_answer(1, ("c1", "c2", "c3"), ("c1", "c3"), "c1")]
    bounds = build_bounds(
        answers,
        {1: ("c4",)},
        BoundConfig(w_top=1.0),
        candidate_ids=("c1", "c2", "c3", "c4", "c5"),
        member_ids=(1,),
    )
    lower = dict(zip(bounds.candidate_ids, bounds.lower[0]))
    upper = dict(zip(bounds.candidate_ids, bounds.upper[0]))

    assert (lower["c1"], upper["c1"]) == (1.0, 2.0)
    assert (lower["c3"], upper["c3"]) == (1.0, 2.0)
    assert (lower["c2"], upper["c2"]) == (0.0, 0.0)
    assert (lower["c4"], upper["c4"]) == (0.0, 0.0)
    assert (lower["c5"], upper["c5"]) == (0.0, 2.0)


def test_estimates_feed_solver_with_unnormalized_scale() -> None:
    candidates = [
        Candidate("c1", 100, "main"),
        Candidate("c2", 200, "main"),
        Candidate("c3", 300, "side"),
        Candidate("c4", 400, "side"),
        Candidate("c5", 500, "main"),
    ]
    constraints = [
        ConfirmedConstraint(
            "budget", ConstraintKind.BUDGET, ConstraintScope.PUBLIC, amount_cents=1000
        ),
        ConfirmedConstraint(
            "exact", ConstraintKind.EXACT_COUNT, ConstraintScope.PUBLIC, min_count=2
        ),
    ]
    model = compile_constraints(constraints, candidates, [Member(1), Member(2)])

    answers = [
        _answer(1, ("c1", "c2", "c3"), ("c1", "c3"), "c1"),
        _answer(2, ("c2", "c3", "c4"), ("c2", "c4"), "c2"),
    ]
    acceptance = rebuild_acceptance(answers, {item.candidate_id: item.category for item in candidates})
    relative = fit_relative_choice(answers)
    estimate = estimate_utilities(acceptance, relative, {}, UtilityConfig())

    result = solve_mean_fair(
        estimate.mu,
        model,
        SolverConfig(lam=Fraction(1, 4), time_limit_seconds=5.0),
    )

    assert result.status.value == "OPTIMAL"
    assert verify_selection(model, result.candidate_ids).ok
    assert math.isfinite(result.objective_float)
