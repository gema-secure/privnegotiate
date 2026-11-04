"""菜单截图识别接口测试（不访问真实 MaaS）。"""

from __future__ import annotations

import base64

import pytest

from app.core.config import settings
from app.services import menu_ocr
from app.api import menu

# 1x1 透明 PNG
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def authorized_ocr(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "menu_ocr_access_token", "test-ocr-credential")
    monkeypatch.setattr(menu, "check_ocr_rate", lambda identity: None)
    client.headers["Authorization"] = "Bearer test-ocr-credential"


def test_not_configured_returns_503(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "maas_api_key", "")
    response = client.post(
        "/menu/parse-image",
        json={"image_base64": PNG_BASE64, "mime_type": "image/png"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "MENU_OCR_NOT_CONFIGURED"


def test_parse_image_normalizes_items(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "maas_api_key", "test-key")
    monkeypatch.setattr(
        menu_ocr,
        "_call_maas",
        lambda data, mime: (
            '```json\n{"items": ['
            '{"name": "宫保鸡丁", "price": "38元", "category": "荤菜"},'
            '{"name": "米饭", "cost_cents": 300},'
            '{"name": "神秘菜"}]}\n```',
            "test-model",
        ),
    )
    response = client.post(
        "/menu/parse-image",
        json={"image_base64": PNG_BASE64, "mime_type": "image/png"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "test-model"
    assert body["items"] == [
        {"name": "宫保鸡丁", "price": "38.00", "category": "荤菜"},
        {"name": "米饭", "price": "3.00", "category": "未分类"},
        {"name": "神秘菜", "price": "0.00", "category": "未分类"},
    ]
    assert len(body["warnings"]) == 1


def test_invalid_base64_returns_422(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "maas_api_key", "test-key")
    response = client.post(
        "/menu/parse-image",
        json={"image_base64": "not-a-real-image!!!", "mime_type": "image/png"},
    )
    assert response.status_code == 422


def test_unsupported_mime_is_rejected(api):
    client, _ = api
    response = client.post(
        "/menu/parse-image",
        json={"image_base64": PNG_BASE64, "mime_type": "image/gif"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("text", ["不是 JSON", "```json\n[]\n```"])
def test_bad_model_output_returns_502(api, monkeypatch, text):
    client, _ = api
    monkeypatch.setattr(settings, "maas_api_key", "test-key")
    monkeypatch.setattr(menu_ocr, "_call_maas", lambda data, mime: (text, "test-model"))
    response = client.post(
        "/menu/parse-image",
        json={"image_base64": PNG_BASE64, "mime_type": "image/png"},
    )
    assert response.status_code == 502
    assert response.json()["code"] == "MENU_OCR_BAD_OUTPUT"
