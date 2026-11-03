from __future__ import annotations

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

app = FastAPI(
    title="私密协商器",
    version=settings.app_version,
    docs_url="/docs" if settings.app_env != "prod" else None,
    redoc_url=None,
)

app.include_router(health_router)
app.include_router(negotiations_router)
app.include_router(me_router)
app.include_router(membership_router)
app.include_router(rounds_router)
app.include_router(concerns_router)
app.include_router(menu_router)


@app.exception_handler(EngineError)
async def engine_error_handler(request: Request, exc: EngineError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message},
    )
