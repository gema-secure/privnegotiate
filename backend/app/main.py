from __future__ import annotations

import logging
import traceback
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.concerns import router as concerns_router
from app.api.health import router as health_router
from app.api.membership import router as membership_router
from app.api.me import router as me_router
from app.api.menu import router as menu_router
from app.api.negotiations import router as negotiations_router
from app.api.rounds import router as rounds_router
from app.core.config import settings
from app.engine.state import EngineError
from app.core.request_limits import MenuBodyLimit

logger = logging.getLogger(__name__)

app = FastAPI(
    title="私密协商器",
    version=settings.app_version,
    docs_url="/docs" if settings.app_env != "prod" else None,
    redoc_url=None,
)
app.add_middleware(MenuBodyLimit, max_bytes=4 * ((settings.menu_image_max_bytes + 2) // 3) + 1024)

app.include_router(health_router)
app.include_router(negotiations_router)
app.include_router(me_router)
app.include_router(membership_router)
app.include_router(rounds_router)
app.include_router(concerns_router)
app.include_router(menu_router)


@app.exception_handler(EngineError)
async def engine_error_handler(request: Request, exc: EngineError) -> JSONResponse:
    logger.warning("engine_error code=%s status=%s method=%s route=%s",
                   exc.code, exc.http_status, request.method, request.url.path)
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error_id = uuid.uuid4().hex
    # Avoid logging SQL parameters, tokens, answers, or exception messages.
    logger.error("unhandled_error id=%s type=%s method=%s route=%s frames=%s",
                 error_id, type(exc).__name__, request.method, request.url.path,
                 [(frame.filename, frame.lineno, frame.name)
                  for frame in traceback.extract_tb(exc.__traceback__)])
    return JSONResponse(status_code=500, content={
        "code": "INTERNAL_ERROR", "message": "服务暂时不可用，请稍后重试", "error_id": error_id,
    })
