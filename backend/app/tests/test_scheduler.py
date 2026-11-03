"""P5 提问启发式：不重复、可参与、覆盖、原因码与确定性。"""

from __future__ import annotations

import pytest

from app.constraints.compiler import compile_constraints
from app.constraints.dsl import (
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member,
)
from app.preference.acceptance import AnswerObservation
from app.preference.estimates import UtilityEstimate
from app.scheduler.heuristic import SchedulerConfig, select_questions
from app.solver.challengers import Challenger, ChallengePool

CATEGORIES = ("main", "side", "drink")


def _model(*, opt_out: tuple[str, ...] = (), excluded: bool = False):
    candidates = [
        Candidate(f"c{index:02d}", 100, CATEGORIES[index % 3]) for index in range(6)
    ]
    members = [Member(1), Member(2)]
    constraints: list[ConfirmedConstraint] = [
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
            min_count=2,
        ),
    ]
    if opt_out:
        constraints.append(
            ConfirmedConstraint(
                constraint_id="opt",
                kind=ConstraintKind.OPT_OUT,
                scope=ConstraintScope.MEMBER,
                owner_member_id=1,
                candidate_ids=opt_out,
            )
        )
    if excluded:
        constraints.append(
            ConfirmedConstraint(
                constraint_id="exclude",
                kind=ConstraintKind.EXCLUDE_ITEM,
                scope=ConstraintScope.PUBLIC,
                candidate_ids=("c03",),
            )
        )
    return compile_constraints(constraints, candidates, members)


def _estimate(mu=None, scale=None) -> UtilityEstimate:
    ids = tuple(f"c{index:02d}" for index in range(6))
    return UtilityEstimate(
        member_ids=(1, 2),
        candidate_ids=ids,
        mu=mu
        or (
            (0.9, 0.8, 0.7, 0.6, 0.5, 0.4),
            (0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
        ),
        scale=scale or ((0.4,) * 6, (0.4,) * 6),
    )


def _pool(challengers=None, incumbent=("c04", "c05")) -> ChallengePool:
    if challengers is None:
        challengers = (
            Challenger("incumbent", incumbent, 0.5, 0.0, "OPTIMAL", tie_break_complete=True),
            Challenger("personal", ("c00", "c01"), 0.45, 0.05, "OPTIMAL", member_id=1),
            Challenger("upper", ("c02", "c03"), 0.4, 0.1, "OPTIMAL"),
        )
    return ChallengePool(
        incumbent_ids=incumbent,
        previous_ids=(),
        member_ids=(1, 2),
        challengers=challengers,
        requested=len(challengers),
        complete=True,
        wall_time=0.0,
        lam=0.25,
    )


def _answers():
    return [
        AnswerObservation(1, ("c00", "c01"), ("c00",), "c00"),
        AnswerObservation(2, ("c02",), ("c02",), "c02"),
    ]


def test_never_repeats_and_respects_participation() -> None:
    model = _model(opt_out=("c04",))
    plan = select_questions(
        _estimate(),
        _pool(),
        _answers(),
        None,
        model,
        SchedulerConfig(batch_size=3),
        round_no=2,
    )
    member1 = plan.members[0]
    ids = [item.candidate_id for item in member1.items]
    assert len(ids) == len(set(ids)) <= 3
    assert "c00" not in ids and "c01" not in ids
    assert "c04" not in ids
    assert all(item.reason in {"challenger", "coverage", "explore"} for item in member1.items)


def test_public_exclusion_is_not_asked() -> None:
    model = _model(excluded=True)
    plan = select_questions(
        _estimate(), _pool(), _answers(), None, model, SchedulerConfig(batch_size=4)
    )
    for member in plan.members:
        assert all(item.candidate_id != "c03" for item in member.items)


def test_batch_always_contains_a_challenger_item_when_available() -> None:
    model = _model()
    plan = select_questions(
        _estimate(),
        _pool(),
        _answers(),
        None,
        model,
        SchedulerConfig(batch_size=2, top_challengers=2),
        round_no=2,
    )
    for member in plan.members:
        mandatory = {"c04", "c05", "c00", "c01", "c02", "c03"}
        assert any(item.candidate_id in mandatory for item in member.items)


def test_first_round_spreads_categories() -> None:
    model = _model()
    plan = select_questions(
        _estimate(),
        _pool(),
        [],
        None,
        model,
        SchedulerConfig(batch_size=4),
        round_no=1,
    )
    for member in plan.members:
        categories = {
            model.categories[model.candidate_index(item.candidate_id)]
            for item in member.items
        }
        assert len(categories) >= 3


def test_priority_equals_diff_times_fairness_factor() -> None:
    model = _model()
    pool = _pool()
    config = SchedulerConfig(
        batch_size=2,
        rho_explore=0.0,
        use_uncertainty=False,
        use_load=False,
        epsilon=0.05,
    )
    plan = select_questions(_estimate(), pool, _answers(), None, model, config)
    member = plan.members[0]
    incumbent_utility = (0.5 + 0.4) / (2 * 2)
    expected_factor = 0.75 + 0.25 / (0.05 + incumbent_utility)
    for item in member.items:
        assert item.priority == pytest.approx(plan.diff_scores[item.candidate_id] * expected_factor)


def test_load_penalizes_the_over_asked_member() -> None:
    model = _model()
    pool = _pool(challengers=(Challenger("incumbent", ("c04", "c05"), 0.5, 0.0, "OPTIMAL"),))
    mu = ((0.5,) * 6, (0.5,) * 6)
    plan = select_questions(
        _estimate(mu=mu),
        pool,
        _answers(),
        {1: 10.0, 2: 0.0},
        model,
        SchedulerConfig(batch_size=6, max_questions=6, beta=1.0),
        round_no=2,
    )
    first = next(
        item.priority
        for member in plan.members
        if member.member_id == 1
        for item in member.items
        if item.candidate_id == "c04"
    )
    second = next(
        item.priority
        for member in plan.members
        if member.member_id == 2
        for item in member.items
        if item.candidate_id == "c04"
    )
    assert first < second


def test_plan_is_deterministic_for_same_seed() -> None:
    model = _model()
    answers = _answers()
    first = select_questions(
        _estimate(), _pool(), answers, None, model, SchedulerConfig(), seed=7, round_no=2
    )
    second = select_questions(
        _estimate(), _pool(), answers, None, model, SchedulerConfig(), seed=7, round_no=2
    )
    assert first.as_dict() == second.as_dict()


def test_member_without_unknown_items_gets_no_batch() -> None:
    model = _model()
    answers = [
        AnswerObservation(1, ("c00", "c01", "c02", "c03", "c04", "c05"), (), None),
        AnswerObservation(2, ("c02",), ("c02",), "c02"),
    ]
    plan = select_questions(_estimate(), _pool(), answers, None, model, SchedulerConfig())
    assert [member.member_id for member in plan.members] == [2]


def test_config_rejects_oversized_batch() -> None:
    with pytest.raises(ValueError):
        SchedulerConfig(batch_size=7)
