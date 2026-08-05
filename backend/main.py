"""Aerolloy Technologies Limited - Enterprise AI Assistant.

FastAPI application entrypoint: middleware, routers, error handling, and the
startup/shutdown lifecycle.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import admin, auth, chat, documents, health
from backend.config.config import settings
from backend.core.constants import APP_DESCRIPTION
from backend.core.exceptions import AppError
from backend.core.logging_config import app_logger, setup_logging
from backend.database.database import check_database_health, dispose_engines
from backend.middleware.request_context import (
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

limiter = Limiter(key_func=get_remote_address, default_limits=[])


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    app_logger.info("=" * 72)
    app_logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION}")
    app_logger.info(f"{settings.ORG_NAME} | environment: {settings.ENVIRONMENT}")
    app_logger.info("=" * 72)

    db_health = await check_database_health()
    if db_health["status"] != "healthy":
        # Loud but not fatal: the container should start so an operator can
        # reach /health and see *why* it is unhealthy.
        app_logger.error(f"Database is not reachable at startup: {db_health}")
    else:
        app_logger.info(f"Database ready (pgvector {db_health.get('pgvector')})")

    from backend.llm.ollama_client import check_ollama_health

    ollama = await check_ollama_health()
    if ollama["status"] == "healthy":
        app_logger.info(
            f"Ollama ready | answer={settings.OLLAMA_MODEL} "
            f"fast={settings.OLLAMA_FAST_MODEL}"
        )
    elif ollama["status"] == "degraded":
        app_logger.warning(f"Ollama models missing: {ollama.get('hint')}")
    else:
        app_logger.error(f"Ollama unreachable: {ollama.get('error')}")

    # Warm the ML models off the event loop. Without this the first user of the
    # day waits ~30 s while embeddings and the reranker load mid-question.
    asyncio.create_task(_warmup())

    yield

    app_logger.info("Shutting down...")
    await dispose_engines()
    app_logger.info("Shutdown complete")


async def _warmup() -> None:
    from backend.embeddings.embedder import get_embedder
    from backend.retrieval.reranker import get_reranker

    try:
        await asyncio.to_thread(get_embedder().warmup)
        app_logger.info("Embedding model warmed up")
    except Exception as exc:
        app_logger.error(f"Embedding warmup failed: {exc}")

    if settings.RERANKER_ENABLED:
        try:
            await asyncio.to_thread(get_reranker().warmup)
            app_logger.info("Reranker warmed up")
        except Exception as exc:
            app_logger.error(f"Reranker warmup failed: {exc}")

    try:
        from backend.agents.graph import get_compiled_graph

        get_compiled_graph()
    except Exception as exc:
        app_logger.error(f"Graph compilation failed: {exc}")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=APP_DESCRIPTION,
    lifespan=lifespan,
    # Interactive docs are a schema disclosure; keep them off in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware runs bottom-up: the last one added is the outermost.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
    max_age=3600,
)
app.add_middleware(RequestContextMiddleware)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Typed domain errors become their declared status and JSON shape."""
    if exc.status_code >= 500:
        app_logger.error(
            f"{exc.error_code} on {request.method} {request.url.path}: {exc.message}"
        )
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Flatten pydantic errors into something a UI can display directly."""
    problems = []
    for error in exc.errors():
        location = " -> ".join(str(part) for part in error["loc"] if part != "body")
        problems.append(
            {"field": location or "request", "message": error.get("msg", "Invalid.")}
        )

    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": problems[0]["message"] if problems else "Invalid request.",
            "details": {"problems": problems},
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "message": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Never leaks a stack trace or internal message to the client."""
    request_id = getattr(request.state, "request_id", "-")
    app_logger.exception(
        f"Unhandled exception on {request.method} {request.url.path} "
        f"(request_id={request_id})"
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": (
                "An unexpected error occurred. Please try again, or contact IT "
                f"quoting reference {request_id}."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(admin.router)


@app.get("/", tags=["Health"], summary="Service banner")
async def root() -> dict[str, str]:
    return {
        "application": settings.APP_NAME,
        "organisation": settings.ORG_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs" if not settings.is_production else "disabled",
    }
