"""P0 冻结契约：本人视图。只允许包含令牌持有者自己的数据。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.commands import (
    AnswerMode,
    MemberConstraintKind,
    NegotiationStatus,
    StrictModel,
)
from app.schemas.public_views import PublicCandidateView, PublicConditionView


class CreatedNegotiationView(StrictModel):
    negotiation_id: int
    member_id: int
    member_token: str
    invite_token: str
    invite_path: str


class JoinedMemberView(StrictModel):
    negotiation_id: int
    member_id: int
    display_name: str
    role: Literal["initiator", "member"]
    member_token: str


class SubmitAnswerResultView(StrictModel):
    batch_id: str
    round_no: int
    mode: AnswerMode
    accepted_at: datetime
    duplicate: bool = False
    negotiation_status: NegotiationStatus


class CloseRoundView(StrictModel):
    round_no: int
    negotiation_status: NegotiationStatus
    result_version: int | None = None
    stable_count: int | None = None
    solver_status: str | None = None
    outcome: Literal["changed", "infeasible", "retry"] = "changed"
    message: str = ""


class OrganizerConcernView(StrictModel):
    concern_id: int
    result_version: int
    text: str
    summary: str | None = None
    summary_source: Literal["llm", "manual", "none"] = "none"
    summary_degraded: bool = False
    summary_degraded_reason: str | None = None
    status: str
    created_at: datetime


class OrganizerDecisionView(StrictModel):
    negotiation_status: NegotiationStatus
    action: str
    concern_id: int | None = None
    round_no: int = 0
    round_issued: bool = False
    outcome: Literal[
        "round_issued",
        "no_new_questions",
        "infeasible",
        "retry",
        "stopped",
    ] = "round_issued"
    message: str = ""


class MemberIdentityView(StrictModel):
    member_id: int
    display_name: str
    role: Literal["initiator", "member"]


class MyQuestionBatchView(StrictModel):
    question_batch_id: str
    round_no: int
    items: list[PublicCandidateView]
    deadline: datetime
    allow_skip: bool = True
    prompt_text: str = ""
    wording_source: Literal["llm", "template"] = "template"
    wording_degraded: bool = False
    wording_degraded_reason: str | None = None


class MyConstraintView(StrictModel):
    constraint_id: str
    kind: MemberConstraintKind
    candidate_ids: list[str] = Field(default_factory=list)
    category: str | None = None
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)
    min_count: int | None = None
    confirmed_version: int


class MyAnswerView(StrictModel):
    batch_id: str
    round_no: int
    mode: AnswerMode
    evaluated_ids: list[str] = Field(default_factory=list)
    acceptable_ids: list[str] = Field(default_factory=list)
    top_id: str | None = None
    accepted_at: datetime


class MyHistoryEntryView(StrictModel):
    round_no: int
    question_batch_id: str
    displayed_ids: list[str]
    answer: MyAnswerView | None = None
    result_version: int | None = None
    result_candidate_ids: list[str] = Field(default_factory=list)
    result_total_cost_cents: int | None = None
    explanation_code: str | None = None


class MyAcceptanceSummaryView(StrictModel):
    explicit_acceptable_count: int
    explicit_unacceptable_count: int
    unknown_count: int
    exact_count: int
    acceptable_lt_k: bool


class MyConfirmationView(StrictModel):
    result_version: int
    decision_config_version: int
    decision: Literal["accept", "reject"] | None = None
    decided_at: datetime | None = None


class MyConcernView(StrictModel):
    concern_id: int
    result_version: int
    text: str
    summary: str | None = None
    summary_source: Literal["llm", "manual", "none"] = "none"
    summary_degraded: bool = False
    summary_degraded_reason: str | None = None
    status: str
    created_at: datetime


class BottomlineProposalView(StrictModel):
    kind: Literal["exclude_item", "opt_out", "require_count"]
    candidate_ids: list[str] = Field(default_factory=list)
    category: str | None = None
    min_count: int | None = None
    display_text: str


class BottomlineParseView(StrictModel):
    """解析提议，必须经成员确认后才可通过 /me/constraints 写入约束。"""

    needs_form: bool
    reason: str | None = None
    proposals: list[BottomlineProposalView] = Field(default_factory=list)
    model: str | None = None
    requires_confirmation: Literal[True] = True
    degraded: bool = False


class MyPendingAttributeCheckView(StrictModel):
    """只返回本人的待确认项，不暴露其他成员的约束或属性需求。"""

    constraint_id: str
    category: str | None = None
    attribute_keys: list[str] = Field(default_factory=list)
    required_count: int
    confirmed_count: int
    unconfirmed_candidate_ids: list[str] = Field(default_factory=list)


class MyStateView(StrictModel):
    negotiation_id: int
    member: MemberIdentityView
    status: NegotiationStatus
    round_no: int
    conditions: PublicConditionView
    decision_config_version: int
    question_batch: MyQuestionBatchView | None = None
    constraints: list[MyConstraintView] = Field(default_factory=list)
    bottomline_status: Literal["pending", "declared", "no_extra"]
    preference_version: int
    acceptance_summary: MyAcceptanceSummaryView
    confirmation: MyConfirmationView | None = None
    concerns: list[MyConcernView] = Field(default_factory=list)
    pending_attribute_checks: list[MyPendingAttributeCheckView] = Field(
        default_factory=list
    )
