"""同预算提问基线：随机、最高不确定性、top-2、纯对称差。"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from app.constraints.compiler import ConstraintModel
from app.preference.acceptance import AnswerObservation
from app.preference.estimates import UtilityEstimate
from app.scheduler.coverage import build_coverage, seeded_order
from app.scheduler.heuristic import (
    MemberQuestionPlan,
    QuestionItem,
    QuestionPlan,
    SchedulerConfig,
    select_questions,
    symmetric_difference_scores,
)
from app.solver.challengers import ChallengePool

STRATEGIES = (
    "heuristic",
    "random",
    "uncertainty",
    "top2",
    "symmetric_difference",
)


def _unknown(
    model: ConstraintModel,
    member_index: int,
    evaluated: Mapping[int, frozenset[str]],
    member_id: int,
) -> list[str]:
    result: list[str] = []
    for index, candidate_id in enumerate(model.candidate_ids):
        if index in model.excluded_indices:
            continue
        if model.e[member_index][index] == 0:
            continue
        if candidate_id in evaluated.get(member_id, frozenset()):
            continue
        result.append(candidate_id)
    return result


def _items(selected: Sequence[str], strategy: str, priority: Mapping[str, float] | None = None) -> tuple[QuestionItem, ...]:
    return tuple(
        QuestionItem(
            candidate_id=item,
            priority=float((priority or {}).get(item, 0.0)),
            reason=f"baseline_{strategy}",
        )
        for item in selected
    )


def make_plan(
    strategy: str,
    estimates: UtilityEstimate,
    pool: ChallengePool,
    active_answers: Sequence[AnswerObservation],
    model: ConstraintModel,
    scheduler: SchedulerConfig | None = None,
    *,
    seed: int = 0,
    round_no: int = 1,
) -> QuestionPlan:
    settings = scheduler or SchedulerConfig()
    if strategy == "heuristic":
        return select_questions(
            estimates,
            pool,
            active_answers,
            None,
            model,
            settings,
            seed=seed,
            round_no=round_no,
        )
    if strategy not in STRATEGIES:
        raise ValueError(f"未知提问基线：{strategy}")

    coverage = build_coverage(active_answers, dict(zip(model.candidate_ids, model.categories)), model.member_ids)
    diff = symmetric_difference_scores(pool, model, settings.eta)
    mu = {
        member_id: dict(zip(estimates.candidate_ids, row))
        for member_id, row in zip(estimates.member_ids, estimates.mu)
    }
    scale = {
        member_id: dict(zip(estimates.candidate_ids, row))
        for member_id, row in zip(estimates.member_ids, estimates.scale)
    }
    global_mean = {
        candidate_id: sum(mu[member_id][candidate_id] for member_id in estimates.member_ids)
        / len(estimates.member_ids)
        for candidate_id in estimates.candidate_ids
    }
    top_ids = {
        candidate_id
        for candidate_id, _ in sorted(global_mean.items(), key=lambda item: (-item[1], item[0]))[:2]
    }

    plans: list[MemberQuestionPlan] = []
    for member_index, member_id in enumerate(model.member_ids):
        unknown = _unknown(model, member_index, coverage.evaluated, member_id)
        if not unknown:
            continue
        rng = np.random.default_rng((seed, round_no, member_id))
        if strategy == "random":
            order = list(unknown)
            rng.shuffle(order)
        elif strategy == "uncertainty":
            order = sorted(unknown, key=lambda item: (-scale[member_id][item], item))
        elif strategy == "top2":
            ranked = sorted(
                unknown,
                key=lambda item: (
                    0 if item in top_ids else 1,
                    -scale[member_id][item],
                    item,
                ),
            )
            order = ranked
        else:
            order = sorted(unknown, key=lambda item: (-diff[item], item))
        selected = order[: settings.batch_size]
        plans.append(
            MemberQuestionPlan(
                member_id=member_id,
                items=_items(selected, strategy, diff if strategy == "symmetric_difference" else None),
                load=coverage.load(member_id, model.member_ids),
                fairness_factor=1.0,
                eligible_unknown=len(unknown),
            )
        )
    return QuestionPlan(
        round_no=round_no,
        members=tuple(plans),
        diff_scores=diff,
        pool_size=len(pool.challengers),
        fallback_reason=None if plans else "NO_UNKNOWN_ITEMS",
    )
