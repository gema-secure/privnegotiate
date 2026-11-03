"""P0 冻结契约：公共投影。禁止出现任何个人答案、理由或效用字段。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.commands import NegotiationStatus, StrictModel


class PublicCandidateView(StrictModel):
    candidate_id: str
    name: str
    cost_cents: int
    category: str
    confirmed_attributes: dict[str, str | int | bool] = Field(default_factory=dict)


class PublicConditionView(StrictModel):
    budget_cents: int
    exact_count: int
    round_limit: int
    category_limits: dict[str, int] = Field(default_factory=dict)
    condition_version: int


class PublicResultView(StrictModel):
    result_version: int
    candidate_ids: list[str]
    total_cost_cents: int
    source: Literal["optimal", "feasible", "carried_over"]
    is_baseline: bool


class PublicNegotiationView(StrictModel):
    negotiation_id: int
    title: str
    status: NegotiationStatus
    round_no: int
    conditions: PublicConditionView
    candidates: list[PublicCandidateView]
    recommendation: PublicResultView | None = None
    recommendation_pending_reason: (
        Literal["no_preference_evidence", "collecting", "computing"] | None
    ) = None
    pending_action: Literal[
        "waiting_members", "waiting_confirmation", "waiting_initiator", "none"
    ] = "none"
