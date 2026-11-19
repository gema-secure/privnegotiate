"""菜单导入：截图识别。"""

from __future__ import annotations

from typing import Literal
import secrets
from threading import BoundedSemaphore

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache.redis import get_client
from app.core.auth import hash_token
from app.core.config import settings
from app.core.deps import bearer_token
from app.db.session import get_db
from app.engine.state import EngineError
from app.models.business import Member

from app.services.menu_ocr import parse_menu_image

router = APIRouter(prefix="/menu", tags=["menu"])
_ocr_slots = BoundedSemaphore(settings.menu_ocr_max_concurrent)


class MenuImageCommand(BaseModel):
    image_base64: str = Field(min_length=16, max_length=4 * ((settings.menu_image_max_bytes + 2) // 3) + 64)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"


class MenuAttributeView(BaseModel):
    key: str
    state: Literal["present", "absent", "unknown"]
    source: Literal["import", "manual"] = "import"


class MenuItemView(BaseModel):
    name: str
    price: str
    category: str
    attributes: list[MenuAttributeView] = Field(default_factory=list)


class MenuImageResult(BaseModel):
    items: list[MenuItemView]
    model: str
    warnings: list[str] = Field(default_factory=list)


def ocr_identity(token: str = Depends(bearer_token), db: Session = Depends(get_db)) -> str:
    digest = hash_token(token)
    if settings.menu_ocr_access_token and secrets.compare_digest(token, settings.menu_ocr_access_token):
        return "operator:" + digest
    member = db.scalars(select(Member).where(Member.token_hash == digest,
        Member.active.is_(True), Member.token_revoked.is_(False))).one_or_none()
    if member is None:
        raise EngineError("UNAUTHORIZED", "请提供有效成员身份或识别凭证", 401)
    return "member:" + str(member.id)


def check_ocr_rate(identity: str) -> None:
    try:
        count = get_client().eval("""
local own = tonumber(redis.call('GET', KEYS[1]) or '0')
local total = tonumber(redis.call('GET', KEYS[2]) or '0')
if own >= tonumber(ARGV[1]) or total >= tonumber(ARGV[2]) then return 0 end
local n = redis.call('INCR', KEYS[1])
local g = redis.call('INCR', KEYS[2])
if n == 1 then redis.call('EXPIRE', KEYS[1], 60) end
if g == 1 then redis.call('EXPIRE', KEYS[2], 60) end
return 1
""", 2, "pn:{ocr}:rate:" + identity, "pn:{ocr}:rate:global",
            settings.menu_ocr_requests_per_minute, settings.menu_ocr_global_requests_per_minute)
    except Exception:
        raise EngineError("MENU_OCR_RATE_UNAVAILABLE", "识别服务暂不可用，请使用文本导入", 503)
    if int(count) != 1:
        raise EngineError("MENU_OCR_RATE_LIMITED", "识别过于频繁，请稍后重试", 429)


@router.post("/parse-image", response_model=MenuImageResult)
def parse_image(command: MenuImageCommand, identity: str = Depends(ocr_identity),
                db: Session = Depends(get_db)) -> MenuImageResult:
    db.rollback()  # Authentication reads must not hold a DB transaction during OCR.
    check_ocr_rate(identity)
    if not _ocr_slots.acquire(blocking=False):
        raise EngineError("MENU_OCR_BUSY", "识别服务繁忙，请稍后重试", 429)
    try:
        result = parse_menu_image(command.image_base64, command.mime_type)
        return MenuImageResult(**result)
    finally:
        _ocr_slots.release()
