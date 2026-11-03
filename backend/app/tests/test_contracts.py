from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.commands import (
    AnswerCommand,
    AnswerMode,
    CandidateInput,
    CreateNegotiationCommand,
    MemberConstraintDraft,
    MemberConstraintKind,
)
from app.schemas.public_views import PublicNegotiationView

PRIVATE_FIELD_NAMES = {
    "answers",
    "acceptable_ids",
    "top_id",
    "reason",
    "utility",
    "mu",
    "sigma",
    "priority",
    "concerns",
    "token",
    "token_hash",
}


def _candidate(index: int) -> CandidateInput:
    return CandidateInput(
        candidate_id=f"c{index}",
        name=f"候选{index}",
        cost_cents=1000 + index,
        category="main",
    )


def _collect_property_names(schema: dict[str, Any]) -> set[str]:
    names = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        names |= set(definition.get("properties", {}))
    return names


def test_public_view_never_exposes_private_fields() -> None:
    schema = PublicNegotiationView.model_json_schema()

    assert not (_collect_property_names(schema) & PRIVATE_FIELD_NAMES)


def test_create_negotiation_requires_unique_candidate_ids() -> None:
    candidates = [_candidate(index) for index in range(13)]
    candidates[1] = candidates[0].model_copy()

    with pytest.raises(ValidationError):
        CreateNegotiationCommand(
            title="周五聚餐",
            initiator_name="组长",
            candidates=candidates,
            budget_cents=50000,
            exact_count=4,
            round_limit=5,
        )


def test_create_negotiation_rejects_out_of_range_candidate_count() -> None:
    with pytest.raises(ValidationError):
        CreateNegotiationCommand(
            title="周五聚餐",
            initiator_name="组长",
            candidates=[_candidate(index) for index in range(12)],
            budget_cents=50000,
            exact_count=4,
            round_limit=5,
        )


def test_answer_requires_acceptable_subset_of_evaluated() -> None:
    with pytest.raises(ValidationError):
        AnswerCommand(
            question_batch_id="qb_1",
            request_id="request-0001",
            mode=AnswerMode.EXPLICIT,
            evaluated_ids=["c1", "c2"],
            acceptable_ids=["c3"],
            top_id="c3",
        )


def test_answer_requires_top_when_acceptable_is_not_empty() -> None:
    with pytest.raises(ValidationError):
        AnswerCommand(
            question_batch_id="qb_1",
            request_id="request-0001",
            mode=AnswerMode.EXPLICIT,
            evaluated_ids=["c1", "c2"],
            acceptable_ids=["c1"],
        )


def test_skip_answer_carries_no_evaluation() -> None:
    with pytest.raises(ValidationError):
        AnswerCommand(
            question_batch_id="qb_1",
            request_id="request-0001",
            mode=AnswerMode.SKIP,
            evaluated_ids=["c1"],
        )

    command = AnswerCommand(
        question_batch_id="qb_1",
        request_id="request-0001",
        mode=AnswerMode.SKIP,
    )
    assert command.top_id is None


def test_member_constraints_reject_public_condition_kinds() -> None:
    with pytest.raises(ValidationError):
        MemberConstraintDraft(kind="budget", min_count=1)

    with pytest.raises(ValidationError):
        MemberConstraintDraft(
            kind=MemberConstraintKind.REQUIRE_COUNT,
            attributes={"vegetarian": True},
        )


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AnswerCommand(
            question_batch_id="qb_1",
            request_id="request-0001",
            mode=AnswerMode.SKIP,
            utility=0.8,
        )
