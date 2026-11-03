"""菜单截图识别：调用华为云 MaaS 视觉模型，把截图转成候选列表。

只处理用户主动上传的图片，不抓取任何第三方平台数据；图片仅在内存中处理，
不落盘、不入库。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

import httpx

from app.core.config import settings
from app.engine.state import EngineError

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

SYSTEM_PROMPT = (
    "你是菜单识别助手。用户会给你一张菜单照片或外卖、团购页面截图。"
    "请提取其中所有可点选的菜品、饮品或套餐，输出严格的 JSON，不要输出任何解释。"
    "格式：{\"items\":[{\"name\":\"菜名\",\"price\":38.5,\"category\":\"荤菜\"}]}。"
    "要求：name 使用图中原文，不要翻译、不要加序号；price 为该项的单价数字，单位元，保留两位小数，"
    "看不清或没有标价时省略该字段；category 按菜单自身的分组填写，没有分组时用「未分类」。"
    "忽略店铺名、地址、评分、优惠规则、配送费说明等非菜品文字。"
    "如果图中是同一种菜品的多个规格或套餐，按图中实际可点的条目逐条输出。"
)


def _normalize_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        cleaned = re.sub(r"[^\d.]", "", str(value))
        if not cleaned:
            return None
        try:
            amount = float(cleaned)
        except ValueError:
            return None
    if amount <= 0 or amount > 100000:
        return None
    return round(amount, 2)


def _decode_image(image_base64: str, mime_type: str) -> bytes:
    if mime_type not in ALLOWED_MIME:
        raise EngineError(
            "MENU_IMAGE_TYPE_UNSUPPORTED",
            "仅支持 JPG、PNG、WebP 格式的菜单截图。",
            422,
        )
    payload = image_base64.strip()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise EngineError("MENU_IMAGE_INVALID", "图片数据不完整，请重新选择截图。", 422)
    if not data:
        raise EngineError("MENU_IMAGE_INVALID", "图片数据为空，请重新选择截图。", 422)
    if len(data) > settings.menu_image_max_bytes:
        limit_mb = settings.menu_image_max_bytes // (1024 * 1024)
        raise EngineError(
            "MENU_IMAGE_TOO_LARGE",
            f"图片超过 {limit_mb}MB，请压缩或裁剪菜单区域后重试。",
            413,
        )
    return data


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise EngineError(
        "MENU_OCR_BAD_OUTPUT",
        "识别服务返回的内容无法解析，请换一张更清晰的截图。",
        502,
    )


def _call_maas(image_data: bytes, mime_type: str) -> tuple[str, str]:
    if not settings.maas_api_key:
        raise EngineError(
            "MENU_OCR_NOT_CONFIGURED",
            "截图识别尚未配置（缺少 MAAS_API_KEY）。请先使用 CSV、JSON 或粘贴文本导入。",
            503,
        )
    encoded = base64.b64encode(image_data).decode("ascii")
    body = {
        "model": settings.maas_vision_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张菜单截图，只输出 JSON。"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 2048,
    }
    url = f"{settings.maas_base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {settings.maas_api_key}"},
            timeout=settings.maas_timeout_seconds,
        )
    except httpx.HTTPError:
        raise EngineError(
            "MENU_OCR_UNREACHABLE",
            "暂时连不上识别服务，请稍后重试，或改用 CSV、JSON 导入。",
            502,
        )
    if response.status_code >= 400:
        raise EngineError(
            "MENU_OCR_FAILED",
            f"识别服务返回错误（{response.status_code}），请稍后重试或改用文本导入。",
            502,
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise EngineError(
            "MENU_OCR_BAD_OUTPUT", "识别服务返回格式异常，请重试。", 502
        )
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content), settings.maas_vision_model


def parse_menu_image(image_base64: str, mime_type: str) -> dict[str, Any]:
    image_data = _decode_image(image_base64, mime_type)
    content, model = _call_maas(image_data, mime_type)
    document = _extract_json(content)
    if not isinstance(document, dict):
        raise EngineError(
            "MENU_OCR_BAD_OUTPUT", "识别结果格式不正确，请换一张截图重试。", 502
        )

    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        raise EngineError(
            "MENU_OCR_BAD_OUTPUT", "识别结果里没有菜品列表，请换一张截图重试。", 502
        )

    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("名称") or "").strip()
        if not name:
            continue
        price = raw.get("price")
        if price is None:
            price = raw.get("cost")
        if price is None and raw.get("cost_cents") is not None:
            cents = _normalize_price(raw.get("cost_cents"))
            price = cents / 100 if cents is not None else None
        amount = _normalize_price(price)
        if amount is None:
            warnings.append(f"「{name}」未识别到有效价格，已按 0 处理，请手动补充。")
            amount = 0
        category = str(raw.get("category") or raw.get("分类") or "").strip()
        items.append(
            {"name": name[:120], "price": f"{amount:.2f}", "category": category[:64] or "未分类"}
        )

    if not items:
        raise EngineError(
            "MENU_OCR_EMPTY", "没有从图片中识别到菜品，请确认截图包含完整菜单。", 422
        )
    return {"items": items, "model": model, "warnings": warnings}
