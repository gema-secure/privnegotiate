"""集合顶项模型：强收缩 PL 的 MAP 与 Laplace 方差。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from app.preference.acceptance import AnswerObservation

Choice = tuple[tuple[int, ...], int]


@dataclass(frozen=True, slots=True)
class RelativeChoicePrior:
    sigma_v: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.sigma_v) or self.sigma_v <= 0:
            raise ValueError("σ_v 必须为正")


@dataclass(frozen=True, slots=True)
class NumericConfig:
    max_iterations: int = 100
    gradient_tolerance: float = 1e-6
    armijo: float = 1e-4
    line_search_steps: int = 60
    damping_start: float = 1e-10
    damping_trials: int = 12

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations 必须为正")
        if self.gradient_tolerance <= 0:
            raise ValueError("梯度容差必须为正")


@dataclass(frozen=True, slots=True)
class MemberRelativeState:
    member_id: int
    candidate_ids: tuple[str, ...]
    means: tuple[float, ...]
    variances: tuple[float, ...]
    iterations: int
    gradient_norm: float | None
    converged: bool
    used_fallback: bool
    status: str


@dataclass(frozen=True, slots=True)
class RelativeChoiceState:
    members: Mapping[int, MemberRelativeState]
    prior_variance: float

    def posterior(self, member_id: int, candidate_id: str) -> tuple[float, float]:
        state = self.members.get(member_id)
        if state is None or state.used_fallback:
            return 0.0, self.prior_variance
        try:
            index = state.candidate_ids.index(candidate_id)
        except ValueError:
            return 0.0, self.prior_variance
        return state.means[index], state.variances[index]

    def member_state(self, member_id: int) -> MemberRelativeState | None:
        return self.members.get(member_id)


def _validate_choices(choices: Sequence[Choice]) -> None:
    for indices, top in choices:
        if not indices or top not in indices:
            raise ValueError("每个有效回答必须给出可接受集合与其中顶项")


def pl_objective(v: Sequence[float], choices: Sequence[Choice], sigma_v: float) -> float:
    vector = np.asarray(v, dtype=float)
    sigma_sq = sigma_v * sigma_v
    objective = float(vector @ vector) / (2 * sigma_sq)
    for indices, top in choices:
        sub = vector[list(indices)]
        maximum = float(sub.max())
        objective += maximum + math.log(float(np.exp(sub - maximum).sum()))
        objective -= float(vector[top])
    return objective


def pl_gradient(
    v: Sequence[float], choices: Sequence[Choice], sigma_v: float
) -> np.ndarray:
    vector = np.asarray(v, dtype=float)
    gradient = vector / (sigma_v * sigma_v)
    for indices, top in choices:
        sub = vector[list(indices)]
        shifted = np.exp(sub - sub.max())
        probabilities = shifted / shifted.sum()
        gradient[list(indices)] += probabilities
        gradient[top] -= 1.0
    return gradient


def pl_hessian(
    v: Sequence[float], choices: Sequence[Choice], sigma_v: float
) -> np.ndarray:
    vector = np.asarray(v, dtype=float)
    dimension = vector.size
    hessian = np.eye(dimension) / (sigma_v * sigma_v)
    for indices, _ in choices:
        sub = vector[list(indices)]
        shifted = np.exp(sub - sub.max())
        probabilities = shifted / shifted.sum()
        block = np.diag(probabilities) - np.outer(probabilities, probabilities)
        hessian[np.ix_(indices, indices)] += block
    return hessian


def _solve_damped(
    matrix: np.ndarray, rhs: np.ndarray, numeric: NumericConfig
) -> np.ndarray | None:
    dimension = matrix.shape[0]
    scale = max(1.0, float(np.trace(matrix)) / dimension)
    identity = np.eye(dimension)
    for trial in range(numeric.damping_trials):
        damping = numeric.damping_start * (10.0**trial) * scale
        try:
            factor = np.linalg.cholesky(matrix + damping * identity)
            return np.linalg.solve(factor.T, np.linalg.solve(factor, rhs))
        except np.linalg.LinAlgError:
            continue
    return None


def _effective_universe(
    active_answers: Sequence[AnswerObservation],
) -> tuple[dict[int, list[tuple[tuple[str, ...], str]]], dict[int, set[str]]]:
    choices: dict[int, list[tuple[tuple[str, ...], str]]] = {}
    universe: dict[int, set[str]] = {}
    for answer in active_answers:
        if answer.mode != "explicit" or not answer.acceptable_ids:
            continue
        acceptable = tuple(sorted(set(answer.acceptable_ids)))
        universe.setdefault(answer.member_id, set()).update(acceptable)
        if len(acceptable) < 2 or answer.top_id is None:
            continue
        if answer.top_id not in acceptable:
            raise ValueError("top_id 必须属于可接受集合")
        choices.setdefault(answer.member_id, []).append((acceptable, answer.top_id))
    return choices, universe


def _fallback_state(
    member_id: int,
    candidate_ids: tuple[str, ...],
    prior: RelativeChoicePrior,
    *,
    iterations: int,
    gradient_norm: float | None,
    status: str,
) -> MemberRelativeState:
    return MemberRelativeState(
        member_id=member_id,
        candidate_ids=candidate_ids,
        means=tuple(0.0 for _ in candidate_ids),
        variances=tuple(prior.sigma_v**2 for _ in candidate_ids),
        iterations=iterations,
        gradient_norm=gradient_norm,
        converged=False,
        used_fallback=True,
        status=status,
    )


def _fit_member(
    member_id: int,
    candidate_ids: tuple[str, ...],
    choices: Sequence[Choice],
    prior: RelativeChoicePrior,
    numeric: NumericConfig,
) -> MemberRelativeState:
    if not choices:
        return _fallback_state(
            member_id,
            candidate_ids,
            prior,
            iterations=0,
            gradient_norm=None,
            status="no_effective_answers",
        )

    position = {candidate_id: index for index, candidate_id in enumerate(candidate_ids)}
    local_choices = tuple(
        (tuple(position[item] for item in acceptable), position[top])
        for acceptable, top in choices
    )
    vector = np.zeros(len(candidate_ids))
    objective = pl_objective(vector, local_choices, prior.sigma_v)
    gradient_norm: float | None = None
    iterations = 0
    status = "max_iterations"
    converged = False

    for iteration in range(1, numeric.max_iterations + 1):
        iterations = iteration
        gradient = pl_gradient(vector, local_choices, prior.sigma_v)
        gradient_norm = float(np.linalg.norm(gradient))
        if not math.isfinite(gradient_norm):
            status = "numerical_failure"
            break
        if gradient_norm <= numeric.gradient_tolerance:
            converged = True
            status = "converged"
            break
        hessian = pl_hessian(vector, local_choices, prior.sigma_v)
        step = _solve_damped(hessian, -gradient, numeric)
        if step is None:
            status = "numerical_failure"
            break
        slope = float(gradient @ step)
        if not math.isfinite(slope) or slope >= 0:
            status = "numerical_failure"
            break
        step_size = 1.0
        accepted = False
        for _ in range(numeric.line_search_steps):
            trial = vector + step_size * step
            trial_objective = pl_objective(trial, local_choices, prior.sigma_v)
            if math.isfinite(trial_objective) and (
                trial_objective <= objective + numeric.armijo * step_size * slope
            ):
                vector = trial
                objective = trial_objective
                accepted = True
                break
            step_size *= 0.5
        if not accepted:
            status = "line_search_failed"
            break

    if not converged and status == "max_iterations":
        gradient_norm = float(
            np.linalg.norm(pl_gradient(vector, local_choices, prior.sigma_v))
        )
        if gradient_norm <= numeric.gradient_tolerance:
            converged = True
            status = "converged"

    if not converged:
        return _fallback_state(
            member_id,
            candidate_ids,
            prior,
            iterations=iterations,
            gradient_norm=gradient_norm,
            status=status,
        )

    hessian = pl_hessian(vector, local_choices, prior.sigma_v)
    covariance = _solve_damped(hessian, np.eye(len(candidate_ids)), numeric)
    if covariance is None or not np.all(np.isfinite(vector)):
        return _fallback_state(
            member_id,
            candidate_ids,
            prior,
            iterations=iterations,
            gradient_norm=gradient_norm,
            status="numerical_failure",
        )

    variances = np.clip(np.diag(covariance), 0.0, prior.sigma_v**2)
    return MemberRelativeState(
        member_id=member_id,
        candidate_ids=candidate_ids,
        means=tuple(float(value) for value in vector),
        variances=tuple(float(value) for value in variances),
        iterations=iterations,
        gradient_norm=gradient_norm,
        converged=True,
        used_fallback=False,
        status="converged",
    )


def fit_relative_choice(
    active_answers: Sequence[AnswerObservation],
    prior_config: RelativeChoicePrior | None = None,
    numeric_config: NumericConfig | None = None,
) -> RelativeChoiceState:
    """每个成员独立拟合；|A|≤1 的回答不提供相对偏好证据。"""
    prior = prior_config or RelativeChoicePrior()
    numeric = numeric_config or NumericConfig()
    choices, universe = _effective_universe(active_answers)

    members: dict[int, MemberRelativeState] = {}
    for member_id in sorted(universe):
        candidate_ids = tuple(sorted(universe[member_id]))
        members[member_id] = _fit_member(
            member_id,
            candidate_ids,
            tuple(choices.get(member_id, ())),
            prior,
            numeric,
        )
    return RelativeChoiceState(
        members=MappingProxyType(members), prior_variance=prior.sigma_v**2
    )
