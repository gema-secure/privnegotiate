"""固定种子模拟实例：潜在偏好、费用与可复现约束。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np


@dataclass(frozen=True, slots=True)
class InstanceParams:
    members: int = 4
    candidates: int = 20
    exact_count: int = 2
    categories: int = 5
    correlation: Literal["shared", "heterogeneous", "independent"] = "shared"
    concentration: float = 6.0
    preference_scale: float = 1.0
    minority: bool = False
    budget_slack_cents: int = 2000
    opt_out_rate: float = 0.0
    w_top: float = 1.0
    pair_bonus: float = 0.0

    def __post_init__(self) -> None:
        if not 3 <= self.members <= 15:
            raise ValueError("成员数必须在 3–15 之间")
        if not 13 <= self.candidates <= 30:
            raise ValueError("候选数必须在 13–30 之间")
        if not 1 <= self.exact_count <= min(8, self.candidates):
            raise ValueError("恰选数量必须在 1–8 之间且不超过候选数")
        if self.categories < 1:
            raise ValueError("类别数必须为正")
        if self.concentration <= 0 or self.preference_scale <= 0:
            raise ValueError("集中度与偏好尺度必须为正")
        if self.correlation not in ("shared", "heterogeneous", "independent"):
            raise ValueError("未知的同类相关模式")
        if not math.isfinite(self.w_top) or self.w_top < 0:
            raise ValueError("w_top 必须是非负有限数")
        if not math.isfinite(self.pair_bonus) or not 0 <= self.pair_bonus <= 1:
            raise ValueError("pair_bonus 必须在 [0,1] 内")


@dataclass(frozen=True, slots=True)
class SimInstance:
    seed: int
    member_ids: tuple[int, ...]
    candidate_ids: tuple[str, ...]
    categories: tuple[str, ...]
    costs_cents: tuple[int, ...]
    budget_cents: int
    exact_count: int
    acceptance: tuple[tuple[bool, ...], ...]
    strength: tuple[tuple[float, ...], ...]
    mu: tuple[tuple[float, ...], ...]
    opt_out: tuple[tuple[int, str], ...]
    params: InstanceParams
    pair_bonus: float = 0.0

    def member_index(self, member_id: int) -> int:
        return self.member_ids.index(member_id)

    def candidate_index(self, candidate_id: str) -> int:
        return self.candidate_ids.index(candidate_id)

    def category_of(self, candidate_id: str) -> str:
        return self.categories[self.candidate_index(candidate_id)]


def _acceptance_matrix(
    rng: np.random.Generator, params: InstanceParams
) -> np.ndarray:
    members = params.members
    items = params.candidates
    category_index = np.array([index % params.categories for index in range(items)])
    probabilities = rng.beta(
        params.concentration * 0.6,
        params.concentration * 0.4,
        size=(members, params.categories),
    )
    if params.correlation == "independent":
        item_probability = np.full((members, items), 0.5)
    else:
        item_probability = probabilities[:, category_index]
        if params.correlation == "heterogeneous":
            item_probability = np.clip(
                item_probability + rng.normal(0.0, 0.25, size=(members, items)),
                0.02,
                0.98,
            )
    if params.minority and members >= 2:
        item_probability[-1] = np.clip(item_probability[-1] * 0.4, 0.02, 0.98)
    return rng.random((members, items)) < item_probability


def generate_instance(seed: int, params: InstanceParams | None = None) -> SimInstance:
    settings = params or InstanceParams()
    rng = np.random.default_rng(seed)
    member_ids = tuple(range(1, settings.members + 1))
    candidate_ids = tuple(f"c{index:02d}" for index in range(1, settings.candidates + 1))
    categories = tuple(
        f"cat{index % settings.categories}" for index in range(settings.candidates)
    )
    costs = tuple(
        int(value) for value in rng.integers(500, 5001, size=settings.candidates)
    )
    cheapest = sum(sorted(costs)[: settings.exact_count])
    budget = min(sum(costs), cheapest + int(settings.budget_slack_cents))

    acceptance = _acceptance_matrix(rng, settings)
    strength = rng.normal(0.0, settings.preference_scale, size=(settings.members, settings.candidates))
    mu = np.zeros((settings.members, settings.candidates))
    for member in range(settings.members):
        for item in range(settings.candidates):
            if not acceptance[member, item]:
                continue
            mu[member, item] = 1.0 + settings.w_top / (1.0 + math.exp(-float(strength[member, item])))

    opt_out: list[tuple[int, str]] = []
    if settings.opt_out_rate > 0:
        for member_index, member_id in enumerate(member_ids):
            for item_index, candidate_id in enumerate(candidate_ids):
                if rng.random() < settings.opt_out_rate:
                    opt_out.append((member_id, candidate_id))
                    mu[member_index, item_index] = 0.0
            if len(opt_out) > settings.members * settings.candidates // 10:
                break

    return SimInstance(
        seed=seed,
        member_ids=member_ids,
        candidate_ids=candidate_ids,
        categories=categories,
        costs_cents=costs,
        budget_cents=budget,
        exact_count=settings.exact_count,
        acceptance=tuple(tuple(bool(value) for value in row) for row in acceptance),
        strength=tuple(tuple(float(value) for value in row) for row in strength),
        mu=tuple(tuple(float(value) for value in row) for row in mu),
        opt_out=tuple(opt_out),
        params=settings,
        pair_bonus=settings.pair_bonus,
    )


def drift_instance(
    instance: SimInstance,
    seed: int,
    fraction: float = 0.2,
    change_rate: float = 0.5,
) -> SimInstance:
    """在固定费用与候选下修改部分成员的部分偏好真值。

    仅真值变化，问题包与约束模型保持同一几何；用于模拟协商中途改主意。
    """
    if not 0 < fraction <= 1:
        raise ValueError("漂移成员比例必须在 (0,1] 内")
    if not 0 <= change_rate <= 1:
        raise ValueError("漂移项比例必须在 [0,1] 内")

    members = instance.params.members
    items = instance.params.candidates
    rng = np.random.default_rng((seed, 0xD21F7))
    count = max(1, min(members, round(fraction * members)))
    changed_members = rng.choice(members, size=count, replace=False)

    acceptance = np.array(instance.acceptance, dtype=bool)
    strength = np.array(instance.strength, dtype=float)
    for member in changed_members:
        mask = rng.random(items) < change_rate
        if not mask.any():
            mask[rng.integers(0, items)] = True
        acceptance[member, mask] = ~acceptance[member, mask]
        strength[member, mask] = rng.normal(
            0.0, instance.params.preference_scale, size=int(mask.sum())
        )

    mu = np.zeros((members, items))
    for member in range(members):
        for item in range(items):
            if acceptance[member, item]:
                mu[member, item] = 1.0 + instance.params.w_top / (
                    1.0 + math.exp(-float(strength[member, item]))
                )
    for member_id, candidate_id in instance.opt_out:
        mu[instance.member_index(member_id), instance.candidate_index(candidate_id)] = 0.0

    return replace(
        instance,
        acceptance=tuple(tuple(bool(value) for value in row) for row in acceptance),
        strength=tuple(tuple(float(value) for value in row) for row in strength),
        mu=tuple(tuple(float(value) for value in row) for row in mu),
    )
