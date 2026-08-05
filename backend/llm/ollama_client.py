"""Ollama LLM factory.

Two models, chosen per job:

* ``OLLAMA_MODEL`` (large) - writes the final grounded answer. Quality here is
  what the user judges the system on.
* ``OLLAMA_FAST_MODEL`` (small) - the classification and rewriting steps.
  These run on *every* question and are simple enough that a 3B model handles
  them, so putting them on the large model would roughly double latency for no
  accuracy gain.

Temperature is pinned to 0 everywhere. This is a factual retrieval assistant
for a manufacturing business; sampling variety is a defect, not a feature.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, TypeVar

import httpx
from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ValidationError

from backend.config.config import settings
from backend.core.exceptions import LLMUnavailableError
from backend.core.logging_config import app_logger

T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=4)
def _build_llm(
    model: str, temperature: float, num_ctx: int, json_mode: bool
) -> ChatOllama:
    return ChatOllama(
        model=model,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=temperature,
        num_ctx=num_ctx,
        format="json" if json_mode else None,
        client_kwargs={"timeout": settings.OLLAMA_TIMEOUT},
        # Deterministic decoding. top_k=1 makes num_predict the only source of
        # variation, which keeps regression tests meaningful.
        top_k=1,
        top_p=1.0,
        repeat_penalty=1.05,
    )


def get_llm(*, json_mode: bool = False, num_ctx: int | None = None) -> ChatOllama:
    """The answering model."""
    return _build_llm(
        settings.OLLAMA_MODEL,
        settings.OLLAMA_TEMPERATURE,
        num_ctx or settings.OLLAMA_NUM_CTX,
        json_mode,
    )


def get_fast_llm(*, json_mode: bool = True, num_ctx: int | None = None) -> ChatOllama:
    """The routing/rewriting/grading model."""
    return _build_llm(
        settings.OLLAMA_FAST_MODEL,
        0.0,
        num_ctx or 4096,
        json_mode,
    )


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response.

    Small models wrap JSON in prose or fences even in JSON mode often enough
    that parsing defensively is cheaper than retrying.
    """
    raw = raw.strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # First balanced {...} span.
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(raw)):
        if raw[index] == "{":
            depth += 1
        elif raw[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def astructured(
    messages: list[BaseMessage],
    schema: type[T],
    *,
    fast: bool = True,
    default: T | None = None,
) -> T:
    """Call the LLM and parse the reply into ``schema``.

    Returns ``default`` on any failure rather than raising. Every caller of
    this is a *decision* node (is this in scope? is this grounded?), and the
    defaults are chosen to be the safe answer, so a flaky model degrades the
    system's helpfulness rather than its correctness.
    """
    llm = get_fast_llm(json_mode=True) if fast else get_llm(json_mode=True)

    try:
        response = await llm.ainvoke(messages)
    except httpx.ConnectError as exc:
        raise LLMUnavailableError(
            f"Cannot reach Ollama at {settings.OLLAMA_BASE_URL}. "
            f"Is the service running?"
        ) from exc
    except Exception as exc:
        app_logger.warning(f"Structured LLM call failed: {exc}")
        if default is not None:
            return default
        raise LLMUnavailableError(str(exc)) from exc

    content = response.content if isinstance(response.content, str) else str(
        response.content
    )
    payload = _extract_json(content)

    if payload is None:
        app_logger.warning(
            f"Could not parse JSON from {schema.__name__} response: {content[:200]!r}"
        )
        if default is not None:
            return default
        raise LLMUnavailableError("Model returned unparseable output.")

    try:
        return schema(**payload)
    except ValidationError as exc:
        app_logger.warning(f"{schema.__name__} validation failed: {exc}")
        if default is not None:
            return default
        raise LLMUnavailableError("Model returned output in an unexpected shape.") from exc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def check_ollama_health() -> dict[str, Any]:
    """Confirm Ollama is up and the configured models are actually pulled."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            response.raise_for_status()
            payload = response.json()

        available = {m.get("name", "") for m in payload.get("models", [])}
        # Ollama reports "qwen2.5:14b-instruct"; tolerate an implicit :latest.
        def _present(name: str) -> bool:
            return name in available or f"{name}:latest" in available

        missing = [
            name
            for name in (settings.OLLAMA_MODEL, settings.OLLAMA_FAST_MODEL)
            if not _present(name)
        ]

        return {
            "status": "healthy" if not missing else "degraded",
            "base_url": settings.OLLAMA_BASE_URL,
            "answer_model": settings.OLLAMA_MODEL,
            "fast_model": settings.OLLAMA_FAST_MODEL,
            "missing_models": missing,
            "hint": (
                "Run: " + "; ".join(f"ollama pull {m}" for m in missing)
                if missing
                else None
            ),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "base_url": settings.OLLAMA_BASE_URL,
            "error": str(exc),
        }
