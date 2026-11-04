from __future__ import annotations

from fastapi import APIRouter, Response

from app.cache.redis import ping_redis
from app.core.config import settings
from app.db.session import ping_db

router = APIRouter()


@router.get("/healthz")
def healthz(response: Response) -> dict[str, str]:
    status = {
        "app": "ok",
        "db": "ok" if ping_db() else "error",
        "redis": "ok" if ping_redis() else "error",
        "version": settings.app_version,
    }
    if status["db"] != "ok" or status["redis"] != "ok":
        response.status_code = 503
    return status
