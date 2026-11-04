import json
from pathlib import Path
import re

from app.main import app

ROOT = Path(__file__).resolve().parents[3]


def test_openapi_contract_matches_reviewed_snapshot():
    snapshot = Path(__file__).parent / "snapshots/openapi.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()


def test_frontend_enums_match_backend_contract():
    types = (ROOT / "frontend/src/types.ts").read_text(encoding="utf-8")
    schemas = app.openapi()["components"]["schemas"]
    for ts_name, backend_name in [("NegotiationStatus", "NegotiationStatus"),
                                  ("AnswerMode", "AnswerMode"), ("ConstraintKind", "MemberConstraintKind")]:
        declaration = re.search(rf"export type {ts_name}\s*=([^;]+);", types).group(1)
        assert set(re.findall(r'"([^"]+)"', declaration)) == set(schemas[backend_name]["enum"])
