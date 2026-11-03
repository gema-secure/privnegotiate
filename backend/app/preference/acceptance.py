"""类别接受预测：Beta-Bernoulli，只统计本人有效明确标签。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

LabelKey = tuple[int, str]


@dataclass(frozen=True, slots=True)
class AnswerObservation:
    """一条已接受的有效回答；输入顺序即事实顺序。"""

    member_id: int
    evaluated_ids: tuple[str, ...] = ()
    acceptable_ids: tuple[str, ...] = ()
    top_id: str | None = None
    mode: str = "explicit"

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluated_ids", tuple(self.evaluated_ids))
        object.__setattr__(self, "acceptable_ids", tuple(self.acceptable_ids))
        if self.mode not in ("explicit", "skip"):
            raise ValueError("mode 只能是 explicit 或 skip")
        if self.mode == "skip":
            if self.evaluated_ids or self.acceptable_ids or self.top_id is not None:
                raise ValueError("跳过不携带任何评价")
            return
        evaluated = set(self.evaluated_ids)
        if len(evaluated) != len(self.evaluated_ids):
            raise ValueError("evaluated_ids 不能重复")
        acceptable = set(self.acceptable_ids)
        if len(acceptable) != len(self.acceptable_ids):
            raise ValueError("acceptable_ids 不能重复")
        if not acceptable <= evaluated:
            raise ValueError("acceptable_ids 必须是 evaluated_ids 的子集")
        if acceptable:
            if self.top_id is None or self.top_id not in acceptable:
                raise ValueError("有可接受项时 top_id 必须属于其中")
        elif self.top_id is not None:
            raise ValueError("无可接受项时 top_id 必须为空")


@dataclass(frozen=True, slots=True)
class AcceptancePrior:
    p0: float = 0.5
    kappa: float = 2.0

    def __post_init__(self) -> None:
        if not 0 < self.p0 < 1:
            raise ValueError("p0 必须在 (0,1) 内")
        if not math.isfinite(self.kappa) or self.kappa <= 0:
            raise ValueError("κ 必须为正，避免 0/0")


@dataclass(frozen=True, slots=True)
class CategoryAcceptance:
    member_id: int
    category: str
    n: int
    s: int
    alpha: float
    beta: float
    p_hat: float
    var_p: float


@dataclass(frozen=True, slots=True)
class AcceptanceState:
    candidate_categories: Mapping[str, str]
    labels: Mapping[LabelKey, bool]
    category_states: Mapping[tuple[int, str], CategoryAcceptance]
    prior: AcceptancePrior
    member_ids: tuple[int, ...] = ()

    def category_state(self, member_id: int, category: str) -> CategoryAcceptance:
        found = self.category_states.get((member_id, category))
        if found is not None:
            return found
        alpha = self.prior.kappa * self.prior.p0
        beta = self.prior.kappa * (1 - self.prior.p0)
        return CategoryAcceptance(
            member_id=member_id,
            category=category,
            n=0,
            s=0,
            alpha=alpha,
            beta=beta,
            p_hat=self.prior.p0,
            var_p=alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1)),
        )

    def p_and_variance(self, member_id: int, candidate_id: str) -> tuple[float, float]:
        label = self.labels.get((member_id, candidate_id))
        if label is not None:
            return (1.0 if label else 0.0), 0.0
        category = self.candidate_categories.get(candidate_id)
        state = self.category_state(member_id, category or "unknown")
        return state.p_hat, state.p_hat * (1 - state.p_hat)


def normalize_categories(candidate_categories: Mapping[str, str]) -> Mapping[str, str]:
    normalized = {}
    for candidate_id, category in candidate_categories.items():
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate_id 必须是非空字符串")
        normalized[candidate_id] = str(category) if category else "unknown"
    return MappingProxyType(normalized)


def current_explicit_labels(
    active_answers: Iterable[AnswerObservation],
    candidate_categories: Mapping[str, str] | None = None,
) -> dict[LabelKey, bool]:
    """按输入顺序取每个成员对每个候选的最新明确标签。"""
    labels: dict[LabelKey, bool] = {}
    for answer in active_answers:
        if answer.mode != "explicit":
            continue
        acceptable = set(answer.acceptable_ids)
        for candidate_id in answer.evaluated_ids:
            if candidate_categories is not None and candidate_id not in candidate_categories:
                raise ValueError(f"回答引用未知候选：{candidate_id}")
            labels[(answer.member_id, candidate_id)] = candidate_id in acceptable
    return labels


def rebuild_acceptance(
    active_answers: Sequence[AnswerObservation],
    candidate_categories: Mapping[str, str],
    prior_config: AcceptancePrior | None = None,
) -> AcceptanceState:
    """从有效事件重建计数；重复提交与重放不增加 n。"""
    prior = prior_config or AcceptancePrior()
    categories = normalize_categories(candidate_categories)
    labels = current_explicit_labels(active_answers, categories)

    members = sorted({member_id for member_id, _ in labels})
    counters: dict[tuple[int, str], list[int]] = {}
    for (member_id, candidate_id), accepted in labels.items():
        category = categories[candidate_id]
        slot = counters.setdefault((member_id, category), [0, 0])
        slot[0] += 1
        slot[1] += 1 if accepted else 0

    category_states: dict[tuple[int, str], CategoryAcceptance] = {}
    for (member_id, category), (n, s) in counters.items():
        alpha = prior.kappa * prior.p0 + s
        beta = prior.kappa * (1 - prior.p0) + n - s
        category_states[(member_id, category)] = CategoryAcceptance(
            member_id=member_id,
            category=category,
            n=n,
            s=s,
            alpha=alpha,
            beta=beta,
            p_hat=alpha / (alpha + beta),
            var_p=alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1)),
        )

    return AcceptanceState(
        candidate_categories=categories,
        labels=MappingProxyType(dict(labels)),
        category_states=MappingProxyType(category_states),
        prior=prior,
        member_ids=tuple(members),
    )


def normalize_opt_out(
    opt_out: Mapping[int, Iterable[str]] | None,
) -> Mapping[int, frozenset[str]]:
    if not opt_out:
        return MappingProxyType({})
    normalized = {
        int(member_id): frozenset(str(item) for item in candidate_ids)
        for member_id, candidate_ids in opt_out.items()
    }
    return MappingProxyType(normalized)
