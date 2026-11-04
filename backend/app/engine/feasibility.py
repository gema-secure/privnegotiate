"""Freeze and commit inputs, solve outside locks, then publish using version CAS."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.batches import create_round_batches
from app.engine.closing import begin_round_close, run_round_computation
from app.engine.events import append_event
from app.engine.state import conflict
from app.models.business import Member, Negotiation


def start_round(db: Session, negotiation: Negotiation, *, actor_member_id=None, ranking=None) -> str:
    negotiation = db.get(Negotiation, negotiation.id, with_for_update=True, populate_existing=True)
    members = db.scalars(select(Member).where(
        Member.negotiation_id == negotiation.id, Member.active.is_(True)
    )).all()
    if not members or any(m.bottomline_status == "pending" for m in members):
        negotiation.active_result_version = None
        negotiation.status = "COLLECTING_BOTTOMLINE" if members else "CLOSED_UNCONFIRMED"
        append_event(db, negotiation, "FEASIBILITY_CHECKED", {
            "status": negotiation.status, "outcome": "bottomline_pending" if members else "no_members",
        }, actor_member_id=actor_member_id)
        return negotiation.status

    actor = next((m for m in members if m.id == actor_member_id), members[0])
    snapshot = begin_round_close(db, negotiation, actor, initial=True)
    computation = run_round_computation(snapshot)
    negotiation = db.get(Negotiation, snapshot.negotiation_id, with_for_update=True, populate_existing=True)
    if (negotiation.status != "CHECKING_FEASIBILITY"
            or negotiation.round_no != snapshot.round_no
            or negotiation.closing_job_version != snapshot.state_version
            or negotiation.constraint_version != snapshot.constraint_version
            or negotiation.decision_config_version != snapshot.decision_config_version
            or negotiation.membership_version != snapshot.membership_version):
        raise conflict("ROUND_COMMIT_RACE", "开轮输入已变化，请重新读取状态")
    if computation.outcome == "feasible":
        negotiation.membership_frozen = True
        next_round = snapshot.round_no + 1
        created = create_round_batches(db, negotiation, round_no=next_round,
            candidates=snapshot.candidates, members=snapshot.members,
            constraints=snapshot.constraints, ranking=ranking, plan=computation.question_plan)
        negotiation.status = "ASKING" if created else "BEST_EFFORT"
        if created:
            negotiation.round_no = next_round
    elif computation.outcome == "infeasible":
        negotiation.status = "INFEASIBLE"
    else:
        negotiation.status = "RETRY_OR_REVIEW"
    append_event(db, negotiation, "FEASIBILITY_CHECKED", {
        "outcome": computation.solver_status or computation.outcome,
        "status": negotiation.status, "round_no": negotiation.round_no,
    }, actor_member_id=actor_member_id)
    db.commit()
    return negotiation.status
