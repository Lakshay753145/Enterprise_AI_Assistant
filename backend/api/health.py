"""Health and readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from backend.config.config import settings
from backend.core.constants import API_PREFIX
from backend.database.database import check_database_health
from backend.llm.ollama_client import check_ollama_health

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Cheap - answers as long as the process is up. Safe for load balancers."""
    return {
        "status": "healthy",
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get(f"{API_PREFIX}/health/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    """Checks the dependencies a chat request actually needs.

    Returns 503 when a dependency is down so an orchestrator stops routing
    traffic here rather than serving users a broken chatbot.
    """
    database = await check_database_health()
    ollama = await check_ollama_health()

    healthy = database["status"] == "healthy" and ollama["status"] in (
        "healthy",
        "degraded",
    )
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if healthy else "not_ready",
        "checks": {"database": database, "ollama": ollama},
    }
