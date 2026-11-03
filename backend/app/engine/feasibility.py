"""底线收齐后的可行性检查与开局派题。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.compiler import CompileError, compile_constraints
from app.constraints.validation import precheck
from app.engine.batches import create_round_batches
from app.engine.events import append_event
from app.engine.loading import (
    active_answers,
    answer_observations,
    candidate_dtos,
    constraint_dtos,
    member_dtos,
    opt_out_map,
)
from app.engine.scheduling import build_scheduling
from app.models.business import (
    Candidate,
    ConfirmedConstraint,
    Member,
    Negotiation,
)
from app.preference.acceptance import AcceptancePrior, rebuild_acceptance
from app.preference.estimates import UtilityConfig, estimate_utilities
from app.preference.relative_choice import RelativeChoicePrior, fit_relative_choice
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolveStatus

from app.engine.closing import solver_config


def start_round(
    db: Session,
    negotiation: Negotiation,
    *,
    actor_member_id: int | None = None,
    ranking=None,
) -> str:
    """在已确认约束上检查可行域并开启下一轮；返回新状态。"""
    candidates = db.scalars(
        select(Candidate)
        .where(Candidate.negotiation_id == negotiation.id)
        .order_by(Candidate.public_id)
    ).all()
    members = db.scalars(
        select(Member).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ).all()
    constraints = db.scalars(
        select(ConfirmedConstraint).where(
            ConfirmedConstraint.negotiation_id == negotiation.id,
            ConfirmedConstraint.active.is_(True),
        )
    ).all()

    if not members:
        negotiation.active_result_version = None
        negotiation.status = "CLOSED_UNCONFIRMED"
        return negotiation.status
    if any(member.bottomline_status == "pending" for member in members):
        negotiation.active_result_version = None
        negotiation.status = "COLLECTING_BOTTOMLINE"
        return negotiation.status

    negotiation.status = "CHECKING_FEASIBILITY"
    try:
        model = compile_constraints(
            constraint_dtos(constraints),
            candidate_dtos(candidates),
            member_dtos(members),
        )
    except CompileError as exc:
        negotiation.status = "RETRY_OR_REVIEW"
        append_event(
            db,
            negotiation,
            "FEASIBILITY_CHECKED",
            {"outcome": "model_invalid", "detail": str(exc)},
            actor_member_id=actor_member_id,
        )
        return negotiation.status

    report = precheck(model)
    if report.definitely_infeasible:
        negotiation.status = "INFEASIBLE"
        append_event(
            db,
            negotiation,
            "FEASIBILITY_CHECKED",
            {
                "outcome": "infeasible",
                "codes": [issue.code for issue in report.issues],
            },
            actor_member_id=actor_member_id,
        )
        return negotiation.status

    zero_mu = [[0.0] * model.candidate_count for _ in range(model.member_count)]
    result = solve_mean_fair(zero_mu, model, solver_config(negotiation))
    if result.status is SolveStatus.INFEASIBLE:
        negotiation.status = "INFEASIBLE"
    elif result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
        negotiation.membership_frozen = True
        next_round = negotiation.round_no + 1
        candidate_tuple = candidate_dtos(candidates)
        member_tuple = member_dtos(members)
        constraint_tuple = constraint_dtos(constraints)
        opt_out = opt_out_map(constraint_tuple)
        answers = answer_observations(active_answers(db, negotiation.id))
        config = negotiation.config_json or {}
        beta = config.get("beta") or {}
        pl = config.get("pl") or {}
        w_top = float(config.get("w_top", 1.0))
        acceptance = rebuild_acceptance(
            answers,
            {item.candidate_id: item.category for item in candidate_tuple if item.active},
            AcceptancePrior(
                p0=float(beta.get("p0", 0.5)),
                kappa=float(beta.get("kappa", 2.0)),
            ),
        )
        relative = fit_relative_choice(
            answers, RelativeChoicePrior(sigma_v=float(pl.get("sigma_v", 1.0)))
        )
        estimate = estimate_utilities(
            acceptance,
            relative,
            opt_out,
            UtilityConfig(
                w_top=w_top,
                quadrature_nodes=int(config.get("quadrature_nodes", 15)),
            ),
            0,
            extra_member_ids=[item.member_id for item in member_tuple],
        )
        artifacts = build_scheduling(
            estimate,
            answers,
            model,
            opt_out,
            result.candidate_ids,
            solver_config(negotiation),
            config,
            round_no=next_round,
        )
        created = create_round_batches(
            db,
            negotiation,
            round_no=next_round,
            candidates=candidate_tuple,
            members=member_tuple,
            constraints=constraint_tuple,
            ranking=ranking,
            plan=artifacts.plan,
        )
        if created:
            negotiation.round_no = next_round
            negotiation.status = "ASKING"
        else:
            negotiation.status = "BEST_EFFORT"
    else:
        negotiation.status = "RETRY_OR_REVIEW"

    append_event(
        db,
        negotiation,
        "FEASIBILITY_CHECKED",
        {
            "outcome": result.status.value,
            "status": negotiation.status,
            "round_no": negotiation.round_no,
        },
        actor_member_id=actor_member_id,
    )
    return negotiation.status
