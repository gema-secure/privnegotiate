"""菜单导入：截图识别。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.menu_ocr import parse_menu_image

router = APIRouter(prefix="/menu", tags=["menu"])


class MenuImageCommand(BaseModel):
    image_base64: str = Field(min_length=16)
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


@router.post("/parse-image", response_model=MenuImageResult)
def parse_image(command: MenuImageCommand) -> MenuImageResult:
    result = parse_menu_image(command.image_base64, command.mime_type)
    return MenuImageResult(**result)
