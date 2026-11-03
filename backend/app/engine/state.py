"""统一状态常量、错误与时间处理。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.commands import NegotiationStatus

TERMINAL_STATUSES = frozenset(
    {
        NegotiationStatus.FINALIZED.value,
        NegotiationStatus.CLOSED_WITH_CONCERN.value,
        NegotiationStatus.CLOSED_UNCONFIRMED.value,
    }
)

ANSWER_OPEN_STATUSES = frozenset(
    {NegotiationStatus.ASKING.value, NegotiationStatus.ANSWERING.value}
)

CONFIRM_STATUSES = frozenset(
    {NegotiationStatus.PRIVATE_CONFIRM.value, NegotiationStatus.BEST_EFFORT.value}
)


class EngineError(Exception):
    """带业务错误码的冲突；由 API 层统一转成 JSON 响应。"""

    def __init__(self, code: str, message: str, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def conflict(code: str, message: str) -> EngineError:
    return EngineError(code, message, 409)


def invalid(code: str, message: str) -> EngineError:
    return EngineError(code, message, 422)


def forbidden(code: str, message: str) -> EngineError:
    return EngineError(code, message, 403)


def not_found(code: str, message: str) -> EngineError:
    return EngineError(code, message, 404)


def utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def require_status(negotiation, allowed: frozenset[str] | set[str], code: str) -> None:
    if negotiation.status not in allowed:
        raise conflict(code, f"当前状态 {negotiation.status} 不允许该操作")


def has_evidence(answers) -> bool:
    return any(
        answer.mode == "explicit" and bool(answer.evaluated_ids)
        for answer in answers
    )
