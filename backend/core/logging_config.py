"""Structured logging + the on-disk long-term history.

Two distinct concerns live here:

1. **Operational logging** (loguru) - rotating files under logs/, split by
   concern so an auditor can look at exactly one stream.

2. **Long-term conversation history** - every question and answer is appended
   as JSON Lines to logs/chat/<Department>/<username>/<YYYY-MM>.jsonl. This is
   the durable, greppable transcript archive the database is not: it survives a
   DB restore, it is trivially exportable, and it is partitioned by department
   so an auditor reviewing one department never reads another's.

Log directory layout::

    logs/
      app/         application_YYYY-MM-DD.log     general runtime
      error/       error_YYYY-MM-DD.log           WARNING and above
      access/      access_YYYY-MM-DD.log          HTTP request lines
      audit/       audit_YYYY-MM-DD.jsonl         security events (JSONL)
      ingestion/   ingestion_YYYY-MM-DD.log       document processing
      chat/        <Dept>/<user>/<YYYY-MM>.jsonl  conversation archive
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from backend.config.config import settings

_configured = False
_chat_write_lock = threading.Lock()
_audit_write_lock = threading.Lock()

# Subdirectories created under LOG_DIR.
_SUBDIRS = ("app", "error", "access", "audit", "ingestion", "chat")

_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[scope]}</cyan> | "
    "<level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[scope]: <10} | "
    "{name}:{function}:{line} | {message}"
)


def _log_root() -> Path:
    root = settings.log_path
    for sub in _SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _scope_is(name: str):
    """Filter factory: keep only records tagged with this scope."""

    def _filter(record: dict[str, Any]) -> bool:
        return record["extra"].get("scope") == name

    return _filter


def setup_logging() -> None:
    """Configure loguru sinks. Safe to call more than once."""
    global _configured
    if _configured:
        return

    root = _log_root()
    logger.remove()

    # Every record gets a `scope` so filters and formats never KeyError.
    logger.configure(extra={"scope": "app"})

    retention = f"{settings.LOG_RETENTION_DAYS} days"

    # --- Console -------------------------------------------------------------
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=settings.DEBUG,
        diagnose=settings.DEBUG,
        enqueue=True,
    )

    # --- General application log --------------------------------------------
    logger.add(
        root / "app" / "application_{time:YYYY-MM-DD}.log",
        level=settings.LOG_LEVEL,
        format=_FILE_FORMAT,
        rotation=settings.LOG_ROTATION,
        retention=retention,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # --- Errors only (never rotated away early - this is the incident trail) -
    logger.add(
        root / "error" / "error_{time:YYYY-MM-DD}.log",
        level="WARNING",
        format=_FILE_FORMAT,
        rotation=settings.LOG_ROTATION,
        retention=f"{max(settings.LOG_RETENTION_DAYS, 365)} days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=False,  # never serialise local variables - they hold secrets
        enqueue=True,
    )

    # --- HTTP access ---------------------------------------------------------
    logger.add(
        root / "access" / "access_{time:YYYY-MM-DD}.log",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        rotation="1 day",
        retention=retention,
        compression="zip",
        encoding="utf-8",
        filter=_scope_is("access"),
        enqueue=True,
    )

    # --- Ingestion -----------------------------------------------------------
    logger.add(
        root / "ingestion" / "ingestion_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format=_FILE_FORMAT,
        rotation=settings.LOG_ROTATION,
        retention=retention,
        compression="zip",
        encoding="utf-8",
        filter=_scope_is("ingestion"),
        enqueue=True,
    )

    _silence_noisy_third_parties()

    _configured = True
    logger.bind(scope="app").info(
        f"Logging initialised | env={settings.ENVIRONMENT} "
        f"level={settings.LOG_LEVEL} dir={root}"
    )


class _InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, sqlalchemy, transformers) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).bind(
            scope="app"
        ).log(level, record.getMessage())


def _silence_noisy_third_parties() -> None:
    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(settings.LOG_LEVEL)

    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "sqlalchemy.engine",
        "httpx",
        "httpcore",
        "transformers",
        "sentence_transformers",
        "docling",
        "urllib3",
    ):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = [_InterceptHandler()]
        stdlib_logger.propagate = False

    # These are chatty at INFO and drown out anything useful.
    for name in ("httpx", "httpcore", "urllib3", "sentence_transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Scoped logger accessors
# ---------------------------------------------------------------------------

def get_logger(scope: str = "app"):
    """Return a logger bound to a scope (app | access | ingestion | audit)."""
    return logger.bind(scope=scope)


app_logger = logger.bind(scope="app")
access_logger = logger.bind(scope="access")
ingestion_logger = logger.bind(scope="ingestion")


# ---------------------------------------------------------------------------
# Audit trail (JSON Lines)
# ---------------------------------------------------------------------------

def write_audit_event(
    event: str,
    *,
    username: str | None = None,
    user_id: int | None = None,
    department: str | None = None,
    role: str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    success: bool = True,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one security-relevant event to logs/audit/audit_<date>.jsonl.

    Audit records are append-only JSONL rather than free text so they can be
    loaded into a SIEM or analysed with pandas without parsing heroics.
    """
    now = datetime.now(timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "event": event,
        "success": success,
        "username": username,
        "user_id": user_id,
        "department": department,
        "role": role,
        "ip_address": ip_address,
        "request_id": request_id,
        "detail": detail or {},
    }

    path = _log_root() / "audit" / f"audit_{now:%Y-%m-%d}.jsonl"
    line = json.dumps(record, ensure_ascii=False, default=str)

    try:
        with _audit_write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as exc:  # disk full, permissions - must not break the request
        app_logger.error(f"Failed to write audit event {event!r}: {exc}")

    level = "info" if success else "warning"
    getattr(app_logger, level)(
        f"AUDIT {event} user={username or '-'} dept={department or '-'} "
        f"ok={success}"
    )


# ---------------------------------------------------------------------------
# Long-term conversation archive
# ---------------------------------------------------------------------------

def _chat_archive_path(department: str, username: str) -> Path:
    """logs/chat/<Department>/<username>/<YYYY-MM>.jsonl

    Partitioned by department first so filesystem-level access controls can be
    applied per department if the organisation later wants them.
    """
    safe_dept = "".join(c for c in department if c.isalnum() or c in "-_") or "unknown"
    safe_user = "".join(c for c in username if c.isalnum() or c in "-_.") or "unknown"
    directory = _log_root() / "chat" / safe_dept / safe_user
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{datetime.now(timezone.utc):%Y-%m}.jsonl"


def write_chat_archive(
    *,
    username: str,
    user_id: int,
    department: str,
    conversation_id: str,
    message_id: str,
    question: str,
    answer: str,
    answer_source: str,
    citations: list[dict[str, Any]] | None = None,
    timings: dict[str, float] | None = None,
    rewritten_query: str | None = None,
    confidence: float | None = None,
    model: str | None = None,
    request_id: str | None = None,
) -> None:
    """Append a completed exchange to the permanent on-disk transcript.

    Called after the response has been fully streamed, so a failed or aborted
    generation never lands in the archive as if it had succeeded.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "user": {
            "id": user_id,
            "username": username,
            "department": department,
        },
        "question": question,
        "rewritten_query": rewritten_query,
        "answer": answer,
        "answer_source": answer_source,
        "confidence": confidence,
        "model": model,
        "citations": citations or [],
        "timings_ms": timings or {},
    }

    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        path = _chat_archive_path(department, username)
        with _chat_write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as exc:
        app_logger.error(
            f"Failed to archive chat for {username}/{department}: {exc}"
        )


def read_chat_archive(
    department: str,
    username: str,
    *,
    months: int = 12,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Read back a user's archived exchanges, newest first.

    Used by the admin "conversation history" export. Only ever called with a
    department the caller is already authorised for.
    """
    safe_dept = "".join(c for c in department if c.isalnum() or c in "-_")
    safe_user = "".join(c for c in username if c.isalnum() or c in "-_.")
    directory = _log_root() / "chat" / safe_dept / safe_user

    if not directory.is_dir():
        return []

    files = sorted(directory.glob("*.jsonl"), reverse=True)[:months]
    records: list[dict[str, Any]] = []

    for path in files:
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # tolerate a torn final line
        except OSError as exc:
            app_logger.warning(f"Could not read archive {path}: {exc}")

    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records[:limit]
