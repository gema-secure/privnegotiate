"""首版提问启发式：多样挑战池 + 轻量优先级。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.constraints.compiler import ConstraintModel
from app.preference.acceptance import AnswerObservation
from app.preference.estimates import UtilityEstimate
from app.scheduler.coverage import (
    build_coverage,
    least_covered_category,
    seeded_order,
)
from app.solver.challengers import ChallengePool


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    batch_size: int = 4
    max_questions: int = 6
    eta: float = 0.05
    epsilon: float = 0.05
    rho_explore: float = 0.2
    beta: float = 0.5
    top_challengers: int = 3
    use_uncertainty: bool = True
    use_fairness_factor: bool = True
    use_load: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= self.max_questions <= 6:
            raise ValueError("每题项数必须在 1–6 内，且不超过本轮上限")
        for name, value in (
            ("η", self.eta),
            ("ε", self.epsilon),
            ("ρ_explore", self.rho_explore),
            ("β", self.beta),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须是非负有限数")
        if self.top_challengers < 1:
            raise ValueError("top_challengers 至少为 1")


@dataclass(frozen=True, slots=True)
class QuestionItem:
    candidate_id: str
    priority: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "priority": round(self.priority, 6),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MemberQuestionPlan:
    member_id: int
    items: tuple[QuestionItem, ...]
    load: float
    fairness_factor: float
    eligible_unknown: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "items": [item.as_dict() for item in self.items],
            "load": round(self.load, 6),
            "fairness_factor": round(self.fairness_factor, 6),
            "eligible_unknown": self.eligible_unknown,
        }


@dataclass(frozen=True, slots=True)
class QuestionPlan:
    round_no: int
    members: tuple[MemberQuestionPlan, ...]
    diff_scores: Mapping[str, float]
    pool_size: int
    fallback_reason: str | None = None

    def member_items(self, member_id: int) -> tuple[str, ...]:
        for plan in self.members:
            if plan.member_id == member_id:
                return tuple(item.candidate_id for item in plan.items)
        return ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "round_no": self.round_no,
            "members": [item.as_dict() for item in self.members],
            "diff_scores": {key: round(value, 6) for key, value in self.diff_scores.items()},
            "pool_size": self.pool_size,
            "fallback_reason": self.fallback_reason,
        }


def symmetric_difference_scores(
    pool: ChallengePool, model: ConstraintModel, eta: float
) -> dict[str, float]:
    """按与当前推荐的对称差和福利差计算 d_i，并归一化到 [0,1]。"""
    incumbent = set(pool.incumbent_ids)
    raw = {candidate_id: 0.0 for candidate_id in model.candidate_ids}
    for challenger in pool.challengers:
        if challenger.role == "incumbent":
            continue
        chosen = set(challenger.candidate_ids)
        gap = max(0.0, challenger.excess or 0.0)
        weight = 1.0 / (eta + gap)
        for candidate_id in chosen ^ incumbent:
            if candidate_id in raw:
                raw[candidate_id] = max(raw[candidate_id], weight)
    maximum = max(raw.values(), default=0.0)
    if maximum <= 0:
        return {candidate_id: 0.0 for candidate_id in model.candidate_ids}
    return {candidate_id: value / maximum for candidate_id, value in raw.items()}


def _top_challenger_ids(pool: ChallengePool, limit: int) -> set[str]:
    ranked = sorted(
        (item for item in pool.challengers if item.role != "incumbent"),
        key=lambda item: (-(item.objective if item.objective is not None else -1.0), item.candidate_ids),
    )
    result: set[str] = set()
    for challenger in ranked[:limit]:
        result.update(challenger.candidate_ids)
    return result


def _pick_category_spread(
    ordered: Sequence[str],
    limit: int,
    categories: Mapping[str, str],
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for candidate_id in ordered:
        if len(selected) >= limit:
            break
        category = categories.get(candidate_id, "unknown")
        if category in seen:
            continue
        seen.add(category)
        selected.append(candidate_id)
    return selected


def select_questions(
    estimates: UtilityEstimate,
    pool: ChallengePool,
    active_answers: Sequence[AnswerObservation],
    loads: Mapping[int, float] | None,
    model: ConstraintModel,
    config: SchedulerConfig | None = None,
    *,
    seed: int = 0,
    round_no: int = 1,
) -> QuestionPlan:
    """只选本人未评价、可参与、未被整组禁选的候选；不重复凑数。"""
    settings = config or SchedulerConfig()
    if estimates.member_ids != model.member_ids:
        raise ValueError("效用估计成员顺序与约束模型不一致")
    if estimates.candidate_ids != model.candidate_ids:
        raise ValueError("效用估计候选顺序与约束模型不一致")
    if settings.batch_size > settings.max_questions:
        raise ValueError("题包项数超过本轮上限")

    coverage = build_coverage(
        active_answers, dict(zip(model.candidate_ids, model.categories)), model.member_ids
    )
    diff = symmetric_difference_scores(pool, model, settings.eta)
    incumbent = set(pool.incumbent_ids)
    challenger_ids = _top_challenger_ids(pool, settings.top_challengers)
    mandatory = incumbent | challenger_ids
    categories = dict(zip(model.candidate_ids, model.categories))
    excluded = model.excluded_indices

    plans: list[MemberQuestionPlan] = []
    for member_index, member_id in enumerate(model.member_ids):
        eligible_unknown: list[str] = []
        for index, candidate_id in enumerate(model.candidate_ids):
            if index in excluded:
                continue
            if model.e[member_index][index] == 0:
                continue
            if coverage.is_evaluated(member_id, candidate_id):
                continue
            eligible_unknown.append(candidate_id)
        if not eligible_unknown:
            continue

        mu_row = estimates.mu[member_index]
        scale_row = estimates.scale[member_index]
        mu = dict(zip(estimates.candidate_ids, mu_row))
        scale = dict(zip(estimates.candidate_ids, scale_row))
        incumbent_utility = sum(mu[item] for item in incumbent if item in mu) / (
            model.exact_count * (1.0 + estimates.w_top)
        )
        incumbent_utility = min(1.0, max(0.0, incumbent_utility))
        lam = min(1.0, max(0.0, pool.lam))
        factor = 1.0
        if settings.use_fairness_factor:
            factor = min(4.0, max(0.25, (1.0 - lam) + lam / (settings.epsilon + incumbent_utility)))
        load = (
            loads.get(member_id, 0.0)
            if loads is not None
            else coverage.load(member_id, model.member_ids)
        )
        factor_denominator = 1.0 + (settings.beta * load if settings.use_load else 0.0)

        priority: dict[str, float] = {}
        for candidate_id in eligible_unknown:
            value = (diff[candidate_id] + settings.rho_explore) * factor / factor_denominator
            if settings.use_uncertainty:
                value *= scale[candidate_id]
            priority[candidate_id] = value

        order = seeded_order(eligible_unknown, seed, member_id)
        order = sorted(order, key=lambda item: -priority[item])
        selected: list[str] = []
        if round_no == 1:
            selected = _pick_category_spread(order, settings.batch_size, categories)
        for candidate_id in order:
            if len(selected) >= settings.batch_size:
                break
            if candidate_id not in selected:
                selected.append(candidate_id)

        if mandatory and not any(item in mandatory for item in selected):
            forced = next((item for item in order if item in mandatory), None)
            if forced is not None and selected:
                selected[-1] = forced
            elif forced is not None:
                selected.append(forced)

        if len(selected) < settings.batch_size:
            upper_ids = {
                item
                for challenger in pool.challengers
                if challenger.role == "upper"
                for item in challenger.candidate_ids
            }
            reserved = next((item for item in order if item in upper_ids and item not in selected), None)
            if reserved is None:
                category = least_covered_category(eligible_unknown, categories, coverage, pool.incumbent_ids)
                reserved = next(
                    (
                        item
                        for item in order
                        if categories.get(item, "unknown") == category and item not in selected
                    ),
                    None,
                )
            if reserved is not None:
                selected.append(reserved)
        for candidate_id in order:
            if len(selected) >= settings.batch_size:
                break
            if candidate_id not in selected:
                selected.append(candidate_id)

        items = tuple(
            QuestionItem(
                candidate_id=item,
                priority=priority[item],
                reason=_reason(item, selected, mandatory, categories),
            )
            for item in selected[: settings.batch_size]
        )
        plans.append(
            MemberQuestionPlan(
                member_id=member_id,
                items=items,
                load=load,
                fairness_factor=factor,
                eligible_unknown=len(eligible_unknown),
            )
        )

    fallback: str | None = None
    if not plans:
        fallback = "NO_UNKNOWN_ITEMS"
    elif not pool.challengers:
        fallback = "NO_CHALLENGERS"
    return QuestionPlan(
        round_no=round_no,
        members=tuple(plans),
        diff_scores=diff,
        pool_size=len(pool.challengers),
        fallback_reason=fallback,
    )


def _reason(
    candidate_id: str,
    selected: Sequence[str],
    mandatory: set[str],
    categories: Mapping[str, str],
) -> str:
    if candidate_id in mandatory:
        return "challenger"
    category = categories.get(candidate_id, "unknown")
    duplicates = sum(
        1 for item in selected if categories.get(item, "unknown") == category
    )
    return "coverage" if duplicates <= 1 else "explore"
