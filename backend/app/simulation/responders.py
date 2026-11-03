"""固定真值响应者：诚实、选择性低报、全体低报。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from collections.abc import Iterable

from app.preference.acceptance import AnswerObservation
from app.simulation.generators import SimInstance


class Policy(str, Enum):
    HONEST = "honest"
    SELECTIVE = "selective"
    BLANKET = "blanket"


@dataclass(frozen=True, slots=True)
class ResponderConfig:
    policy: Policy = Policy.HONEST
    skip_rate: float = 0.05

    def __post_init__(self) -> None:
        if not 0 <= self.skip_rate <= 1:
            raise ValueError("跳过率必须在 [0,1] 内")


def underreports(policy: Policy, member_id: int) -> bool:
    if policy is Policy.BLANKET:
        return True
    if policy is Policy.SELECTIVE:
        return member_id == 1
    return False


def underreporting_members(policy: Policy, member_ids: Iterable[int]) -> tuple[int, ...]:
    """哪些成员在该人格下低报；供配对度量使用，避免在别处硬编码成员编号。"""
    return tuple(member_id for member_id in member_ids if underreports(policy, member_id))


def answer_batch(
    instance: SimInstance,
    member_id: int,
    displayed_ids: tuple[str, ...],
    config: ResponderConfig,
    *,
    round_no: int,
    seed: int,
) -> AnswerObservation | None:
    """返回本人作答；None 表示跳过（不改变偏好）。"""
    rng = np.random.default_rng((seed, round_no, member_id))
    if rng.random() < config.skip_rate:
        return None
    member = instance.member_index(member_id)
    acceptable: list[str] = []
    for candidate_id in displayed_ids:
        item = instance.candidate_index(candidate_id)
        if instance.acceptance[member][item]:
            acceptable.append(candidate_id)
    if underreports(config.policy, member_id) and acceptable:
        best = max(acceptable, key=lambda cid: (instance.strength[member][instance.candidate_index(cid)], cid))
        acceptable = [best]
    top: str | None = None
    if acceptable:
        top = max(
            acceptable,
            key=lambda cid: (instance.strength[member][instance.candidate_index(cid)], cid),
        )
    return AnswerObservation(
        member_id=member_id,
        evaluated_ids=tuple(displayed_ids),
        acceptable_ids=tuple(acceptable),
        top_id=top,
        mode="explicit",
    )
