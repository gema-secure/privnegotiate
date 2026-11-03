"""成员治理契约；不向其他成员暴露私人底线或个人响应标签。"""
from typing import Literal

from pydantic import Field

from app.schemas.commands import StrictModel
from app.schemas.private_views import MyConstraintView

REMOVAL_NOTICE = "移除仅取消其后续作答与个人效用，已确认的整组禁忌仍将生效"


class MembershipCommand(StrictModel):
    expected_membership_version: int = Field(ge=1)


class LeaveCommand(MembershipCommand):
    acknowledged: Literal[True]


class RemovalCommand(MembershipCommand):
    reason: str = Field(min_length=1, max_length=500)


class RemovalVoteCommand(MembershipCommand):
    approve: bool
    consequences_acknowledged: Literal[True]


class RetainedWithdrawalCommand(StrictModel):
    expected_preference_version: int = Field(ge=1)
    acknowledged: Literal[True]


class RemovalView(StrictModel):
    request_id: int
    target_member_id: int
    membership_version: int
    status: str
    my_vote: bool | None = None
    can_vote: bool = False
    notice: str = REMOVAL_NOTICE


class RosterMemberView(StrictModel):
    member_id: int
    display_name: str
    role: str


class MembershipView(StrictModel):
    membership_version: int
    membership_status: str
    response_status: str
    members: list[RosterMemberView]
    removals: list[RemovalView]


class RetainedConstraintsView(StrictModel):
    membership_status: str
    preference_version: int
    constraints: list[MyConstraintView]
