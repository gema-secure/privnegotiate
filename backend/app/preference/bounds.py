"""独立保守效用区间：只依据已确认输入与规范性刻度。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.preference.acceptance import (
    AnswerObservation,
    current_explicit_labels,
    normalize_opt_out,
)


@dataclass(frozen=True, slots=True)
class BoundConfig:
    w_top: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.w_top) or self.w_top < 0:
            raise ValueError("w_top 必须是非负有限数")


@dataclass(frozen=True, slots=True)
class UtilityBounds:
    """未归一化的单项区间；组合区间需再除以 k(1+w_top)。"""

    member_ids: tuple[int, ...]
    candidate_ids: tuple[str, ...]
    lower: tuple[tuple[float, ...], ...]
    upper: tuple[tuple[float, ...], ...]
    w_top: float = 1.0


def build_bounds(
    active_answers: Sequence[AnswerObservation],
    opt_out: Mapping[int, Iterable[str]] | None,
    config: BoundConfig | None = None,
    *,
    candidate_ids: Sequence[str] | None = None,
    member_ids: Sequence[int] | None = None,
) -> UtilityBounds:
    settings = config or BoundConfig()
    w_top = settings.w_top
    labels = current_explicit_labels(active_answers)
    excluded = normalize_opt_out(opt_out)

    if candidate_ids is None:
        universe = {candidate_id for _, candidate_id in labels}
        for items in excluded.values():
            universe.update(items)
        candidates = tuple(sorted(universe))
    else:
        candidates = tuple(sorted(set(candidate_ids)))
    if member_ids is None:
        members = tuple(sorted({member for member, _ in labels} | set(excluded)))
    else:
        members = tuple(sorted(set(member_ids)))

    lower_rows: list[tuple[float, ...]] = []
    upper_rows: list[tuple[float, ...]] = []
    for member_id in members:
        lower_row: list[float] = []
        upper_row: list[float] = []
        for candidate_id in candidates:
            if candidate_id in excluded.get(member_id, frozenset()):
                lower_row.append(0.0)
                upper_row.append(0.0)
                continue
            label = labels.get((member_id, candidate_id))
            if label is True:
                lower_row.append(1.0)
                upper_row.append(1.0 + w_top)
            elif label is False:
                lower_row.append(0.0)
                upper_row.append(0.0)
            else:
                lower_row.append(0.0)
                upper_row.append(1.0 + w_top)
        lower_rows.append(tuple(lower_row))
        upper_rows.append(tuple(upper_row))

    return UtilityBounds(
        member_ids=members,
        candidate_ids=candidates,
        lower=tuple(lower_rows),
        upper=tuple(upper_rows),
        w_top=w_top,
    )
