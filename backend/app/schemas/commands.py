"""P0 冻结契约：写命令。字段以外的输入一律拒绝。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cents = Annotated[int, Field(ge=0, le=100_000_000)]
CandidateId = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
]
RequestId = Annotated[
    str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NegotiationStatus(str, Enum):
    CREATED = "CREATED"
    COLLECTING_BOTTOMLINE = "COLLECTING_BOTTOMLINE"
    CHECKING_FEASIBILITY = "CHECKING_FEASIBILITY"
    INFEASIBLE = "INFEASIBLE"
    ADJUSTING = "ADJUSTING"
    RETRY_OR_REVIEW = "RETRY_OR_REVIEW"
    ASKING = "ASKING"
    ANSWERING = "ANSWERING"
    CLOSING = "CLOSING"
    COMPUTING = "COMPUTING"
    UPDATING = "UPDATING"
    PRIVATE_CONFIRM = "PRIVATE_CONFIRM"
    BEST_EFFORT = "BEST_EFFORT"
    CONCERN_COLLECTING = "CONCERN_COLLECTING"
    FINALIZED = "FINALIZED"
    CLOSED_WITH_CONCERN = "CLOSED_WITH_CONCERN"
    CLOSED_UNCONFIRMED = "CLOSED_UNCONFIRMED"


class AlgorithmProfile(str, Enum):
    MEAN_FAIR_V1 = "mean_fair_v1"


class CandidateAttributeState(str, Enum):
    """菜品属性的三态事实：已知含 / 已知不含 / 未知。"""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class CandidateAttributeSource(str, Enum):
    IMPORT = "import"
    MANUAL = "manual"
    CORRECTION = "correction"


class CandidateAttributeInput(StrictModel):
    key: str = Field(min_length=1, max_length=64)
    state: CandidateAttributeState
    source: CandidateAttributeSource = CandidateAttributeSource.MANUAL
    source_detail: str | None = Field(default=None, max_length=120)


class CandidateInput(StrictModel):
    candidate_id: CandidateId
    name: str = Field(min_length=1, max_length=120)
    cost_cents: Cents
    category: str = Field(default="unknown", min_length=1, max_length=64)
    confirmed_attributes: dict[str, str | int | bool] = Field(default_factory=dict)
    attributes: list[CandidateAttributeInput] = Field(default_factory=list)

    @field_validator("attributes")
    @classmethod
    def _validate_attributes(
        cls, value: list[CandidateAttributeInput]
    ) -> list[CandidateAttributeInput]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("属性 key 必须唯一")
        if any(
            item.source is CandidateAttributeSource.CORRECTION for item in value
        ):
            raise ValueError("创建或导入时属性来源只能是 import 或 manual")
        return value


class CreateNegotiationCommand(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    initiator_name: str = Field(min_length=1, max_length=32)
    member_count: int = Field(default=3, ge=3)
    """声明的小组人数；低于 3 人拒绝创建，上限由服务端 max_members 约束。"""
    candidates: list[CandidateInput] = Field(min_length=13, max_length=30)
    budget_cents: Cents
    exact_count: int = Field(ge=1, le=8)
    round_limit: int = Field(ge=1, le=20)
    algorithm_profile: AlgorithmProfile = AlgorithmProfile.MEAN_FAIR_V1
    category_limits: dict[str, int] = Field(default_factory=dict)

    @field_validator("candidates")
    @classmethod
    def _unique_candidate_ids(cls, value: list[CandidateInput]) -> list[CandidateInput]:
        ids = [item.candidate_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id 必须唯一")
        return value

    @field_validator("category_limits")
    @classmethod
    def _category_limits_in_range(cls, value: dict[str, int]) -> dict[str, int]:
        if any(limit < 0 or limit > 30 for limit in value.values()):
            raise ValueError("类别上限必须在 0–30 之间")
        return value


class SetCandidateAttributesCommand(StrictModel):
    """发起者人工修正菜品属性，写入修正记录；只改事实，不改成员底线。"""

    attributes: list[CandidateAttributeInput] = Field(min_length=1, max_length=50)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("attributes")
    @classmethod
    def _unique_attribute_keys(
        cls, value: list[CandidateAttributeInput]
    ) -> list[CandidateAttributeInput]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("属性 key 必须唯一")
        return value


class JoinNegotiationCommand(StrictModel):
    invite_token: str = Field(min_length=16, max_length=512)
    display_name: str = Field(min_length=1, max_length=32)


class MemberConstraintKind(str, Enum):
    EXCLUDE_ITEM = "exclude_item"
    OPT_OUT = "opt_out"
    REQUIRE_COUNT = "require_count"


class MemberConstraintDraft(StrictModel):
    constraint_id: str | None = Field(
        default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"
    )
    kind: MemberConstraintKind
    candidate_ids: list[CandidateId] = Field(default_factory=list, max_length=30)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)
    min_count: int | None = Field(default=None, ge=0, le=30)

    @model_validator(mode="after")
    def _parameters_match_kind(self) -> "MemberConstraintDraft":
        if self.kind in (MemberConstraintKind.EXCLUDE_ITEM, MemberConstraintKind.OPT_OUT):
            if not self.candidate_ids:
                raise ValueError("该类型必须指定 candidate_ids")
            if self.category is not None or self.attributes or self.min_count is not None:
                raise ValueError("该类型不接受类别、属性或数量参数")
            if len(set(self.candidate_ids)) != len(self.candidate_ids):
                raise ValueError("candidate_ids 不能重复")
        else:
            if self.candidate_ids:
                raise ValueError("替代要求不使用 candidate_ids")
            if self.min_count is None or self.min_count < 1:
                raise ValueError("替代要求必须给出正数 min_count")
            if self.category is None and not self.attributes:
                raise ValueError("替代要求必须给出类别或属性条件")
        return self


class ConfirmMemberConstraintsCommand(StrictModel):
    """全量替换本人有效底线；未提交的旧底线视为撤销。"""

    constraints: list[MemberConstraintDraft] = Field(default_factory=list, max_length=50)
    no_extra_bottomline: bool = False
    expected_preference_version: int | None = Field(default=None, ge=1)


class BottomlineParseCommand(StrictModel):
    """自然语言底线解析入参；解析结果只作提议，不写入约束。"""

    text: str = Field(min_length=1, max_length=2000)


class AnswerMode(str, Enum):
    EXPLICIT = "explicit"
    SKIP = "skip"


class AnswerCommand(StrictModel):
    question_batch_id: str = Field(min_length=1, max_length=40)
    request_id: RequestId
    mode: AnswerMode
    evaluated_ids: list[CandidateId] = Field(default_factory=list, max_length=6)
    acceptable_ids: list[CandidateId] = Field(default_factory=list, max_length=6)
    top_id: CandidateId | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _set_relations(self) -> "AnswerCommand":
        if self.mode is AnswerMode.SKIP:
            if self.evaluated_ids or self.acceptable_ids or self.top_id is not None:
                raise ValueError("跳过不携带任何评价")
            return self

        evaluated = set(self.evaluated_ids)
        if not evaluated:
            raise ValueError("明确作答必须至少评价一项")
        if len(evaluated) != len(self.evaluated_ids):
            raise ValueError("evaluated_ids 不能重复")
        if not set(self.acceptable_ids) <= evaluated:
            raise ValueError("acceptable_ids 必须是 evaluated_ids 的子集")
        if len(set(self.acceptable_ids)) != len(self.acceptable_ids):
            raise ValueError("acceptable_ids 不能重复")
        if self.acceptable_ids:
            if self.top_id is None:
                raise ValueError("有可接受项时必须选择 top_id")
            if self.top_id not in self.acceptable_ids:
                raise ValueError("top_id 必须属于 acceptable_ids")
        elif self.top_id is not None:
            raise ValueError("无可接受项时 top_id 必须为空")
        return self


class PreferenceRevisionCommand(StrictModel):
    request_id: RequestId
    candidate_id: CandidateId
    acceptable: bool | None


class ConfirmResultCommand(StrictModel):
    decision_config_version: int = Field(ge=1)
    accept: bool
    reason: str | None = Field(default=None, max_length=500)


class CorrectConcernSummaryCommand(StrictModel):
    """发起者对 AI 辅助摘要做人工修正；原文不变。"""

    summary: str = Field(min_length=1, max_length=200)


class SubmitConcernCommand(StrictModel):
    result_version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)
    share_acknowledged: Literal[True]


class OrganizerAction(str, Enum):
    CONTINUE = "continue"
    STOP = "stop"


class PublicAdjustment(StrictModel):
    budget_cents: Cents | None = None
    exact_count: int | None = Field(default=None, ge=1, le=8)
    category_limits: dict[str, int] | None = None

    @model_validator(mode="after")
    def _check(self) -> "PublicAdjustment":
        if all(
            value is None
            for value in (self.budget_cents, self.exact_count, self.category_limits)
        ):
            raise ValueError("至少调整一项公共条件")
        if self.category_limits is not None and any(
            limit < 0 or limit > 30 for limit in self.category_limits.values()
        ):
            raise ValueError("类别上限必须在 0–30 之间")
        return self


class OrganizerDecisionCommand(StrictModel):
    action: OrganizerAction
    concern_id: int | None = Field(default=None, ge=1)
    extra_rounds: int | None = Field(default=None, ge=1, le=10)
    public_adjustment: PublicAdjustment | None = None

    @model_validator(mode="after")
    def _check(self) -> "OrganizerDecisionCommand":
        if self.action is OrganizerAction.CONTINUE:
            if self.extra_rounds is None and self.public_adjustment is None:
                raise ValueError("继续协商必须追加轮数或调整公共条件")
        elif self.extra_rounds is not None or self.public_adjustment is not None:
            raise ValueError("停止决策不接受追加轮数或公共调整")
        return self
