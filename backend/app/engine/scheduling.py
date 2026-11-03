"""把偏好估计、挑战池、保守界与派题计划串成封轮产物。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.constraints.compiler import ConstraintModel
from app.diagnostics.safe_bound import BoundReport, SafeBoundConfig, compute_safe_bound
from app.preference.acceptance import AnswerObservation
from app.preference.bounds import BoundConfig, UtilityBounds, build_bounds
from app.preference.estimates import UtilityEstimate
from app.scheduler.heuristic import QuestionPlan, SchedulerConfig, select_questions
from app.solver.challengers import ChallengeConfig, ChallengePool, build_challengers
from app.solver.mean_fair import SolverConfig


@dataclass(frozen=True, slots=True)
class SchedulingArtifacts:
    pool: ChallengePool
    bound: BoundReport
    plan: QuestionPlan


def scheduler_config(config: Mapping[str, Any], batch_size: int | None = None) -> SchedulerConfig:
    raw = config.get("scheduler") or {}
    return SchedulerConfig(
        batch_size=int(batch_size or config.get("batch_size", 4)),
        max_questions=6,
        eta=float(raw.get("eta", 0.05)),
        epsilon=float(raw.get("epsilon", 0.05)),
        rho_explore=float(raw.get("rho_explore", 0.2)),
        beta=float(raw.get("beta", 0.5)),
        top_challengers=int(raw.get("top_challengers", 3)),
    )


def challenge_config(config: Mapping[str, Any]) -> ChallengeConfig:
    raw = config.get("challenge") or {}
    return ChallengeConfig(
        max_pool=int(raw.get("max_pool", 16)),
        include_personal=bool(raw.get("include_personal", True)),
        include_upper=bool(raw.get("include_upper", True)),
        include_diverse=bool(raw.get("include_diverse", True)),
        time_limit_seconds=float(raw.get("time_limit_seconds", 0.5)),
        random_seed=int(raw.get("random_seed", 0)),
    )


def safe_bound_config(config: Mapping[str, Any], w_top: float) -> SafeBoundConfig:
    raw = config.get("challenge") or {}
    return SafeBoundConfig(
        w_top=w_top,
        time_limit_seconds=float(raw.get("time_limit_seconds", 0.5)),
        random_seed=int(raw.get("random_seed", 0)),
    )


def aligned_mu(
    estimate: UtilityEstimate, model: ConstraintModel
) -> tuple[tuple[float, ...], ...]:
    by_member = {
        member_id: dict(zip(estimate.candidate_ids, row))
        for member_id, row in zip(estimate.member_ids, estimate.mu)
    }
    return tuple(
        tuple(by_member[member_id][candidate_id] for candidate_id in model.candidate_ids)
        for member_id in model.member_ids
    )


def build_scheduling(
    estimate: UtilityEstimate,
    answers: Sequence[AnswerObservation],
    model: ConstraintModel,
    opt_out: Mapping[int, Sequence[str]],
    incumbent: Sequence[str],
    solver_config: SolverConfig,
    config: Mapping[str, Any],
    *,
    round_no: int,
    previous_ids: Sequence[str] | None = None,
) -> SchedulingArtifacts:
    w_top = solver_config.w_top
    bounds = build_bounds(
        answers,
        opt_out,
        BoundConfig(w_top=w_top),
        candidate_ids=model.candidate_ids,
        member_ids=model.member_ids,
    )
    mu = aligned_mu(estimate, model)
    pool = build_challengers(
        mu,
        bounds,
        model,
        incumbent,
        solver_config,
        challenge_config=challenge_config(config),
        previous_incumbent=previous_ids,
    )
    bound = compute_safe_bound(
        bounds,
        incumbent,
        model,
        solver_config,
        safe_bound_config(config, w_top),
        upper_bound=pool.upper_bound,
        upper_status=pool.upper_status or None,
        upper_quantization_bound=pool.upper_quantization_bound,
    )
    plan = select_questions(
        estimate,
        pool,
        answers,
        None,
        model,
        scheduler_config(config),
        seed=int((config.get("challenge") or {}).get("random_seed", 0)),
        round_no=round_no,
    )
    return SchedulingArtifacts(pool=pool, bound=bound, plan=plan)
