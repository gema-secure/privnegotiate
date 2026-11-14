"""封轮：冻结输入 → 纯函数计算 → CAS 提交结果。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from typing import Any, Literal, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constraints.compiler import CompileError, compile_constraints
from app.constraints.dsl import ConfirmedConstraint, Member as DslMember
from app.constraints.dsl import Candidate as DslCandidate
from app.constraints.validation import precheck, verify_selection
from app.core.config import settings
from app.engine.batches import (
    all_deadlines_passed,
    collection_complete,
    create_round_batches,
    mark_batches_closed,
    reopen_batches,
)
from app.engine.events import append_event
from app.engine.loading import (
    active_answers,
    answer_observations,
    candidate_dtos,
    constraint_dtos,
    member_dtos,
    attribute_state_map,
    opt_out_map,
)
from app.engine.scheduling import build_scheduling
from app.engine.state import conflict
from app.models.business import (
    Candidate,
    ConfirmedConstraint as ConstraintRow,
    Member,
    Negotiation,
    RoundResult,
)
from app.models.events import NegotiationEvent
from app.preference.acceptance import AcceptancePrior, rebuild_acceptance
from app.preference.bounds import BoundConfig
from app.preference.estimates import UtilityConfig, estimate_utilities
from app.preference.relative_choice import (
    NumericConfig,
    RelativeChoicePrior,
    fit_relative_choice,
)
from app.scheduler.heuristic import QuestionPlan
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolveResult, SolveStatus, SolverConfig

Outcome = Literal["feasible", "infeasible", "unknown", "invalid"]


@dataclass(frozen=True, slots=True)
class PreviousResult:
    result_version: int
    candidate_ids: tuple[str, ...]
    stable_count: int
    input_event_cutoff: int
    constraint_version: int
    decision_config_version: int
    solver_status: str
    tie_break_complete: bool


@dataclass(frozen=True, slots=True)
class RoundSnapshot:
    negotiation_id: int
    round_no: int
    state_version: int
    constraint_version: int
    decision_config_version: int
    input_event_cutoff: int
    round_limit: int
    config: Mapping[str, Any]
    candidates: tuple[DslCandidate, ...]
    members: tuple[DslMember, ...]
    constraints: tuple[ConfirmedConstraint, ...]
    answers: tuple
    previous: PreviousResult | None
    new_explicit_evidence: bool


@dataclass(frozen=True, slots=True)
class Computation:
    outcome: Outcome
    candidate_ids: tuple[str, ...] = ()
    total_cost_cents: int = 0
    solver_status: str = ""
    objective_quantized: int | None = None
    objective_float: float | None = None
    best_bound: float | None = None
    absolute_gap: float | None = None
    quantization_bound: float | None = None
    tie_break_complete: bool = False
    wall_time: float | None = None
    stable_count: int = 0
    message: str = ""
    scale: Mapping[int, Mapping[str, float]] = field(default_factory=dict)
    challenge_pool: Mapping[str, Any] = field(default_factory=dict)
    bound_report: Mapping[str, Any] = field(default_factory=dict)
    question_plan: QuestionPlan | None = None


def solver_config(negotiation: Negotiation) -> SolverConfig:
    config = negotiation.config_json or {}
    solver = config.get("solver") or {}
    return SolverConfig(
        lam=Fraction(str(config.get("lam", 0.25))),
        w_top=float(config.get("w_top", 1.0)),
        quantization_scale=int(solver.get("quantization_scale", 1000)),
        time_limit_seconds=float(
            solver.get("time_limit_seconds", settings.solver_time_limit_seconds)
        ),
        num_search_workers=int(solver.get("num_search_workers", 1)),
        random_seed=int(solver.get("random_seed", 0)),
        config_version=negotiation.decision_config_version,
    )


def latest_result(db: Session, negotiation_id: int) -> RoundResult | None:
    return db.scalars(
        select(RoundResult)
        .where(RoundResult.negotiation_id == negotiation_id)
        .order_by(RoundResult.result_version.desc())
        .limit(1)
    ).one_or_none()


def _previous(row: RoundResult | None) -> PreviousResult | None:
    if row is None:
        return None
    return PreviousResult(
        result_version=row.result_version,
        candidate_ids=tuple(row.candidate_ids or ()),
        stable_count=row.stable_count,
        input_event_cutoff=row.input_event_cutoff,
        constraint_version=row.constraint_version,
        decision_config_version=row.decision_config_version,
        solver_status=row.solver_status,
        tie_break_complete=row.tie_break_complete,
    )


def _new_explicit_evidence(
    db: Session, negotiation_id: int, cutoff: int
) -> bool:
    rows = db.scalars(
        select(NegotiationEvent).where(
            NegotiationEvent.negotiation_id == negotiation_id,
            NegotiationEvent.id > cutoff,
            NegotiationEvent.type == "ANSWER_SUBMITTED",
        )
    ).all()
    return any((row.payload or {}).get("mode") == "explicit" for row in rows)


def begin_round_close(
    db: Session, negotiation: Negotiation, actor: Member
) -> RoundSnapshot:
    """第一阶段事务：标 CLOSING、冻结截止与版本；迟到回答此后被拒绝。"""
    if negotiation.status not in {"ASKING", "ANSWERING", "RETRY_OR_REVIEW"}:
        raise conflict("ROUND_NOT_CLOSABLE", f"当前状态 {negotiation.status} 不能封轮")

    round_no = negotiation.round_no
    member_rows = db.scalars(
        select(Member).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ).all()
    if actor.role != "initiator":
        if not (
            collection_complete(db, negotiation.id, round_no)
            or all_deadlines_passed(db, negotiation.id, round_no)
        ):
            raise conflict("ROUND_NOT_READY", "尚未收齐且未到截止时间，只有发起者可提前封轮")

    mark_batches_closed(db, negotiation.id, round_no)
    max_event = db.scalar(
        select(func.max(NegotiationEvent.id)).where(
            NegotiationEvent.negotiation_id == negotiation.id
        )
    ) or 0
    negotiation.status = "CLOSING"
    append_event(
        db,
        negotiation,
        "ROUND_CLOSING",
        {"round_no": round_no, "input_event_cutoff": max_event},
        actor_member_id=actor.id,
    )

    candidates = db.scalars(
        select(Candidate)
        .where(Candidate.negotiation_id == negotiation.id)
        .order_by(Candidate.public_id)
    ).all()
    constraints = db.scalars(
        select(ConstraintRow).where(
            ConstraintRow.negotiation_id == negotiation.id,
            ConstraintRow.active.is_(True),
        )
    ).all()
    answers = active_answers(db, negotiation.id)
    previous = latest_result(db, negotiation.id)
    snapshot = RoundSnapshot(
        negotiation_id=negotiation.id,
        round_no=round_no,
        state_version=negotiation.state_version,
        constraint_version=negotiation.constraint_version,
        decision_config_version=negotiation.decision_config_version,
        input_event_cutoff=max_event,
        round_limit=negotiation.round_limit,
        config=dict(negotiation.config_json or {}),
        candidates=candidate_dtos(
            candidates, attribute_state_map(db, negotiation.id)
        ),
        members=member_dtos(member_rows),
        constraints=constraint_dtos(constraints),
        answers=answer_observations(answers),
        previous=_previous(previous),
        new_explicit_evidence=_new_explicit_evidence(
            db, negotiation.id, previous.input_event_cutoff if previous else 0
        ),
    )
    db.commit()
    return snapshot


def compute_round(snapshot: RoundSnapshot) -> Computation:
    """纯计算：只读快照，不访问数据库、缓存、系统时间或 LLM。"""
    try:
        model = compile_constraints(
            snapshot.constraints, snapshot.candidates, snapshot.members
        )
    except CompileError as exc:
        return Computation(outcome="invalid", message=str(exc))

    report = precheck(model)
    if report.definitely_infeasible:
        return Computation(
            outcome="infeasible",
            message="；".join(issue.detail for issue in report.issues),
        )

    config = snapshot.config
    beta = config.get("beta") or {}
    pl = config.get("pl") or {}
    acceptance = rebuild_acceptance(
        snapshot.answers,
        {item.candidate_id: item.category for item in snapshot.candidates if item.active},
        AcceptancePrior(
            p0=float(beta.get("p0", 0.5)),
            kappa=float(beta.get("kappa", 2.0)),
        ),
    )
    relative = fit_relative_choice(
        snapshot.answers,
        RelativeChoicePrior(sigma_v=float(pl.get("sigma_v", 1.0))),
        NumericConfig(),
    )
    opt_out = opt_out_map(snapshot.constraints)
    w_top = float(config.get("w_top", 1.0))
    estimate = estimate_utilities(
        acceptance,
        relative,
        opt_out,
        UtilityConfig(
            w_top=w_top,
            quadrature_nodes=int(config.get("quadrature_nodes", 15)),
        ),
        0,
        extra_member_ids=[member.member_id for member in snapshot.members],
    )

    by_member = {
        member_id: dict(zip(estimate.candidate_ids, row))
        for member_id, row in zip(estimate.member_ids, estimate.mu)
    }
    mu = [
        [by_member[member_id][candidate_id] for candidate_id in model.candidate_ids]
        for member_id in model.member_ids
    ]
    scale = {
        member_id: dict(zip(estimate.candidate_ids, row))
        for member_id, row in zip(estimate.member_ids, estimate.scale)
    }

    solver_config_value = SolverConfig(
        lam=Fraction(str(config.get("lam", 0.25))),
        w_top=w_top,
        quantization_scale=int((config.get("solver") or {}).get("quantization_scale", 1000)),
        time_limit_seconds=float(
            (config.get("solver") or {}).get(
                "time_limit_seconds", settings.solver_time_limit_seconds
            )
        ),
        num_search_workers=int((config.get("solver") or {}).get("num_search_workers", 1)),
        random_seed=int((config.get("solver") or {}).get("random_seed", 0)),
        config_version=snapshot.decision_config_version,
    )
    result = solve_mean_fair(mu, model, solver_config_value)
    computation = _computation_from(snapshot, model, result, scale)
    if computation.outcome != "feasible":
        return computation
    artifacts = build_scheduling(
        estimate,
        snapshot.answers,
        model,
        opt_out,
        computation.candidate_ids,
        solver_config_value,
        snapshot.config,
        round_no=snapshot.round_no + 1,
        previous_ids=snapshot.previous.candidate_ids if snapshot.previous else None,
    )
    return replace(
        computation,
        challenge_pool=artifacts.pool.as_dict(),
        bound_report=artifacts.bound.as_dict(),
        question_plan=artifacts.plan,
    )


def _computation_from(snapshot, model, result: SolveResult, scale) -> Computation:
    # 证据门禁不在这一层：公共投影按库内 explicit 作答判定 no_preference_evidence
    # （见 projection._has_evidence）。封轮层只负责求解状态与结果复核。
    if result.status is SolveStatus.INFEASIBLE:
        return Computation(outcome="infeasible", message=result.message)
    if result.status is SolveStatus.UNKNOWN:
        return Computation(
            outcome="unknown",
            solver_status=result.status.value,
            message=result.message or "有解与无解均未证明",
        )
    if result.status is SolveStatus.MODEL_INVALID:
        return Computation(
            outcome="invalid",
            solver_status=result.status.value,
            message=result.message,
        )

    verification = verify_selection(model, result.candidate_ids)
    if not verification.ok:
        return Computation(
            outcome="invalid",
            solver_status=result.status.value,
            message="结果复核失败：" + "；".join(verification.violations),
        )

    return Computation(
        outcome="feasible",
        candidate_ids=tuple(result.candidate_ids),
        total_cost_cents=verification.total_cost_cents,
        solver_status=result.status.value,
        objective_quantized=result.objective_quantized,
        objective_float=result.objective_float,
        best_bound=result.best_bound,
        absolute_gap=result.absolute_gap,
        quantization_bound=result.quantization_bound,
        tie_break_complete=result.tie_break_complete,
        wall_time=result.wall_time,
        stable_count=_stable_count(snapshot, result),
        message=result.message,
        scale=scale,
    )


def _stable_count(snapshot: RoundSnapshot, result: SolveResult) -> int:
    verified = (
        result.status is SolveStatus.OPTIMAL and result.tie_break_complete
    )
    if not verified:
        return 0
    previous = snapshot.previous
    if previous is None:
        return 1
    same_selection = tuple(sorted(previous.candidate_ids)) == tuple(
        sorted(result.candidate_ids)
    )
    same_versions = (
        previous.constraint_version == snapshot.constraint_version
        and previous.decision_config_version == snapshot.decision_config_version
        and previous.solver_status == SolveStatus.OPTIMAL.value
        and previous.tie_break_complete
    )
    if not (same_selection and same_versions):
        return 1
    if not snapshot.new_explicit_evidence:
        return previous.stable_count
    return min(2, previous.stable_count + 1)


def _checksum(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _audit_record(
    snapshot: RoundSnapshot, computation: Computation
) -> dict[str, Any]:
    inputs = {
        "negotiation_id": snapshot.negotiation_id,
        "round_no": snapshot.round_no,
        "state_version": snapshot.state_version,
        "constraint_version": snapshot.constraint_version,
        "decision_config_version": snapshot.decision_config_version,
        "input_event_cutoff": snapshot.input_event_cutoff,
        "config": dict(snapshot.config),
        "candidates": [
            (item.candidate_id, item.cost_cents, item.category, item.active)
            for item in snapshot.candidates
        ],
        "members": [item.member_id for item in snapshot.members],
        "constraints": [item.constraint_id for item in snapshot.constraints],
        "answers": [asdict(item) for item in snapshot.answers],
    }
    results = {
        "candidate_ids": list(computation.candidate_ids),
        "total_cost_cents": computation.total_cost_cents,
        "solver_status": computation.solver_status,
        "objective_float": computation.objective_float,
        "stable_count": computation.stable_count,
    }
    return {
        "software_version": settings.app_version,
        "algorithm_profile": snapshot.config.get("algorithm_profile", "mean_fair_v1"),
        "solver_status": computation.solver_status,
        "solver_message": computation.message,
        "candidate_order": list(computation.candidate_ids),
        "seed": int((snapshot.config.get("challenge") or {}).get("random_seed", 0)),
        "input_checksum": _checksum(inputs),
        "result_checksum": _checksum(results),
    }


def commit_round(
    db: Session, snapshot: RoundSnapshot, computation: Computation
) -> dict[str, Any]:
    """第三阶段事务：状态/版本 CAS，唯一约束保证只提交一次。"""
    negotiation = db.get(Negotiation, snapshot.negotiation_id, with_for_update=True)
    if (
        negotiation is None
        or negotiation.status != "CLOSING"
        or negotiation.state_version != snapshot.state_version
    ):
        raise conflict("ROUND_COMMIT_RACE", "封轮任务已失效，请重新读取状态")

    if computation.outcome == "infeasible":
        negotiation.status = "INFEASIBLE"
        append_event(
            db,
            negotiation,
            "FEASIBILITY_FAILED",
            {"round_no": snapshot.round_no, "detail": computation.message},
        )
        db.commit()
        return {
            "round_no": snapshot.round_no,
            "negotiation_status": negotiation.status,
            "outcome": "infeasible",
            "message": computation.message,
        }
    if computation.outcome in {"unknown", "invalid"}:
        negotiation.status = "RETRY_OR_REVIEW"
        reopen_batches(db, negotiation.id, snapshot.round_no)
        append_event(
            db,
            negotiation,
            "ROUND_COMPUTE_RETRY",
            {
                "round_no": snapshot.round_no,
                "solver_status": computation.solver_status,
                "detail": computation.message,
            },
        )
        db.commit()
        return {
            "round_no": snapshot.round_no,
            "negotiation_status": negotiation.status,
            "outcome": "retry",
            "message": computation.message,
        }

    result_version = int(
        db.scalar(
            select(func.max(RoundResult.result_version)).where(
                RoundResult.negotiation_id == negotiation.id
            )
        )
        or 0
    ) + 1
    row = RoundResult(
        negotiation_id=negotiation.id,
        round_no=snapshot.round_no,
        result_version=result_version,
        input_event_cutoff=snapshot.input_event_cutoff,
        state_version=snapshot.state_version,
        constraint_version=snapshot.constraint_version,
        decision_config_version=snapshot.decision_config_version,
        candidate_ids=list(computation.candidate_ids),
        total_cost_cents=computation.total_cost_cents,
        solver_status=computation.solver_status,
        objective_quantized=computation.objective_quantized,
        objective_float=computation.objective_float,
        best_bound=computation.best_bound,
        absolute_gap=computation.absolute_gap,
        quantization_bound=computation.quantization_bound,
        tie_break_complete=computation.tie_break_complete,
        wall_time=computation.wall_time,
        stable_count=computation.stable_count,
        audit_ref=f"event:{snapshot.input_event_cutoff}",
        diagnostics={
            "audit": _audit_record(snapshot, computation),
            "challenge_pool": dict(computation.challenge_pool),
            "bound_report": dict(computation.bound_report),
            "question_plan": (
                computation.question_plan.as_dict()
                if computation.question_plan is not None
                else {}
            ),
        },
    )
    db.add(row)
    negotiation.active_result_version = result_version
    append_event(
        db,
        negotiation,
        "ROUND_RESULT_COMMITTED",
        {
            "round_no": snapshot.round_no,
            "result_version": result_version,
            "solver_status": computation.solver_status,
            "stable_count": computation.stable_count,
        },
    )

    if (
        computation.stable_count >= 2
        and computation.solver_status == SolveStatus.OPTIMAL.value
        and computation.tie_break_complete
    ):
        negotiation.status = "PRIVATE_CONFIRM"
        stopping_reason = "TWO_ROUND_STABLE"
    elif snapshot.round_no >= snapshot.round_limit:
        negotiation.status = "BEST_EFFORT"
        stopping_reason = "ROUND_LIMIT"
    else:
        next_round = snapshot.round_no + 1
        created = create_round_batches(
            db,
            negotiation,
            round_no=next_round,
            candidates=snapshot.candidates,
            members=snapshot.members,
            constraints=snapshot.constraints,
            ranking=computation.scale,
            plan=computation.question_plan,
        )
        if created:
            negotiation.round_no = next_round
            negotiation.status = "ASKING"
            stopping_reason = "CONTINUE"
        else:
            negotiation.status = "BEST_EFFORT"
            stopping_reason = "NO_NEW_QUESTIONS"

    diagnostics = dict(row.diagnostics or {})
    diagnostics["stopping_reason"] = stopping_reason
    audit = dict(diagnostics.get("audit") or {})
    audit["stopping_reason"] = stopping_reason
    diagnostics["audit"] = audit
    row.diagnostics = diagnostics
    append_event(
        db,
        negotiation,
        "ROUND_SETTLED",
        {
            "round_no": snapshot.round_no,
            "status": negotiation.status,
            "result_version": result_version,
            "stopping_reason": stopping_reason,
        },
    )
    db.commit()
    return {
        "round_no": snapshot.round_no,
        "negotiation_status": negotiation.status,
        "result_version": result_version,
        "stable_count": computation.stable_count,
        "solver_status": computation.solver_status,
        "outcome": "changed",
        "message": computation.message,
    }
