"""效用均值与提问尺度：接受预测 × sigmoid(v) 的矩。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from app.preference.acceptance import AcceptanceState, normalize_opt_out
from app.preference.relative_choice import RelativeChoiceState

_QUADRATURE_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


@dataclass(frozen=True, slots=True)
class UtilityConfig:
    w_top: float = 1.0
    quadrature_nodes: int = 15

    def __post_init__(self) -> None:
        if not math.isfinite(self.w_top) or self.w_top < 0:
            raise ValueError("w_top 必须是非负有限数")
        if self.quadrature_nodes < 3:
            raise ValueError("求积节点数至少为 3")


@dataclass(frozen=True, slots=True)
class UtilityEstimate:
    member_ids: tuple[int, ...]
    candidate_ids: tuple[str, ...]
    mu: tuple[tuple[float, ...], ...]
    scale: tuple[tuple[float, ...], ...]
    w_top: float = 1.0


def _quadrature(nodes: int) -> tuple[np.ndarray, np.ndarray]:
    cached = _QUADRATURE_CACHE.get(nodes)
    if cached is None:
        cached = np.polynomial.hermite.hermgauss(nodes)
        _QUADRATURE_CACHE[nodes] = cached
    return cached


def _sigmoid_moments(
    mean: float, variance: float, nodes: int
) -> tuple[float, float]:
    if variance <= 0:
        value = 0.5 * (1.0 + math.tanh(mean / 2.0))
        return value, 0.0
    points, weights = _quadrature(nodes)
    standard = math.sqrt(variance)
    z = mean + math.sqrt(2.0) * standard * points
    values = 0.5 * (1.0 + np.tanh(z / 2.0))
    normalization = math.sqrt(math.pi)
    first = float(np.dot(weights, values) / normalization)
    second = float(np.dot(weights, values * values) / normalization)
    return first, max(0.0, second - first * first)


def estimate_utilities(
    acceptance: AcceptanceState,
    relative_choice: RelativeChoiceState,
    opt_out: Mapping[int, Sequence[str]],
    config: UtilityConfig | None = None,
    seed: int = 0,
    *,
    extra_member_ids: Sequence[int] = (),
) -> UtilityEstimate:
    """首版固定节点求积：结果与 seed 无关，仅保留参数并校验。

    seed 为契约 §11.2 预留（v2 场景抽样要用）。首版若误以为 seed 会影响结果，
    会导致"同一输入不同 seed 得到不同推荐"的错误预期，因此这里显式校验而不是静默丢弃。

    extra_member_ids 用于纳入尚无有效回答的成员：这些成员按类别先验与相对偏好先验
    计算，与引擎中的完整成员集合保持一致。
    """
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("seed 必须是非负整数")
    settings = config or UtilityConfig()

    w_top = settings.w_top
    excluded = normalize_opt_out(opt_out)

    member_ids = tuple(
        sorted(
            set(acceptance.member_ids)
            | set(relative_choice.members)
            | set(excluded)
            | {int(member_id) for member_id in extra_member_ids}
        )
    )
    candidate_ids = tuple(sorted(acceptance.candidate_categories))
    mu_rows: list[tuple[float, ...]] = []
    scale_rows: list[tuple[float, ...]] = []

    for member_id in member_ids:
        mu_row: list[float] = []
        scale_row: list[float] = []
        for candidate_id in candidate_ids:
            if candidate_id in excluded.get(member_id, frozenset()):
                mu_row.append(0.0)
                scale_row.append(0.0)
                continue
            p_accept, var_accept = acceptance.p_and_variance(member_id, candidate_id)
            mean_v, var_v = relative_choice.posterior(member_id, candidate_id)
            sig_mean, sig_var = _sigmoid_moments(
                mean_v, max(0.0, var_v), settings.quadrature_nodes
            )
            y_mean = 1.0 + w_top * sig_mean
            y_var = w_top * w_top * sig_var
            mu = p_accept * y_mean
            # 独立乘积的精确二阶矩：Var(a·y)=p·Var(y)+p(1−p)·E[y]²。
            # 跨候选（同类未知项）的相关性未建模，按设计 §4.2 留给 v2 场景抽样；
            # 这只影响组合方差，不影响 mu 与单项 scale。
            var_u = (
                p_accept * p_accept * y_var
                + var_accept * y_mean * y_mean
                + var_accept * y_var
            )
            mu_row.append(min(1.0 + w_top, max(0.0, mu)))
            scale_row.append(min(1.0, math.sqrt(max(0.0, var_u)) / (1.0 + w_top)))
        mu_rows.append(tuple(mu_row))
        scale_rows.append(tuple(scale_row))

    return UtilityEstimate(
        member_ids=member_ids,
        candidate_ids=candidate_ids,
        mu=tuple(mu_rows),
        scale=tuple(scale_rows),
        w_top=w_top,
    )
