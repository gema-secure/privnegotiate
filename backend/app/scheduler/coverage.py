"""覆盖与负担：派题前的确定性统计。"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.preference.acceptance import AnswerObservation


@dataclass(frozen=True, slots=True)
class CoverageState:
    evaluated: Mapping[int, frozenset[str]]
    displayed_counts: Mapping[int, int]
    category_counts: Mapping[str, int]

    def is_evaluated(self, member_id: int, candidate_id: str) -> bool:
        return candidate_id in self.evaluated.get(member_id, frozenset())

    def load(self, member_id: int, member_ids: Sequence[int]) -> float:
        """相对当前成员平均展示数的标准化负担，负数截为 0。"""
        counts = [self.displayed_counts.get(item, 0) for item in member_ids]
        if not counts:
            return 0.0
        average = sum(counts) / len(counts)
        if average <= 0:
            return 0.0
        value = self.displayed_counts.get(member_id, 0)
        return max(0.0, value / average - 1.0)


def build_coverage(
    active_answers: Sequence[AnswerObservation],
    candidate_categories: Mapping[str, str],
    member_ids: Sequence[int],
) -> CoverageState:
    evaluated: dict[int, set[str]] = defaultdict(set)
    displayed: dict[int, int] = {member_id: 0 for member_id in member_ids}
    for answer in active_answers:
        if answer.mode != "explicit":
            continue
        evaluated[answer.member_id].update(answer.evaluated_ids)
        displayed[answer.member_id] = len(evaluated[answer.member_id])
    category_counts: dict[str, int] = defaultdict(int)
    for items in evaluated.values():
        for candidate_id in items:
            category_counts[candidate_categories.get(candidate_id, "unknown")] += 1
    return CoverageState(
        evaluated={member_id: frozenset(items) for member_id, items in evaluated.items()},
        displayed_counts=displayed,
        category_counts=dict(category_counts),
    )


def least_covered_category(
    unknown_ids: Sequence[str],
    candidate_categories: Mapping[str, str],
    coverage: CoverageState,
    incumbent: Sequence[str],
) -> str | None:
    """在未知项中选当前覆盖最少、且不在当前推荐中的类别。"""
    incumbent_set = set(incumbent)
    best: tuple[int, str] | None = None
    for candidate_id in unknown_ids:
        if candidate_id in incumbent_set:
            continue
        category = candidate_categories.get(candidate_id, "unknown")
        key = (coverage.category_counts.get(category, 0), category)
        if best is None or key < best:
            best = key
    return None if best is None else best[1]


def seeded_order(
    candidate_ids: Sequence[str], seed: int, member_id: int
) -> tuple[str, ...]:
    """固定种子的确定性分散顺序，用于同优先级时的稳定打破。

    键公式 (seed << 16) ^ member_id 已随历史实验结果冻结：修改会改变派题顺序，
    使已归档的仿真数据无法按军规 2 复现。
    """
    generator = random.Random((seed << 16) ^ member_id)
    order = list(candidate_ids)
    generator.shuffle(order)
    return tuple(order)
