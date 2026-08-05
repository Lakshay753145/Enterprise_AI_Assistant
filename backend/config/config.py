"""Application settings.

Everything configurable lives here and is sourced from the project-root .env.
Settings are validated at import time so a misconfigured deployment fails fast
and loudly rather than halfway through a user's first question.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: <root>/backend/config/config.py -> <root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    APP_NAME: str = "Aerolloy Enterprise AI Assistant"
    APP_VERSION: str = "2.0.0"
    ORG_NAME: str = "Aerolloy Technologies Limited"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False

    # -------------------------------------------------------------------------
    # Server
    # -------------------------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:5173"

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    LOG_RETENTION_DAYS: int = 90
    LOG_ROTATION: str = "50 MB"

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    MAX_LOGIN_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    SQL_AGENT_DATABASE_URL: str = ""
    SQL_AGENT_ENABLED: bool = True
    SQL_AGENT_MAX_ROWS: int = 50

    # -------------------------------------------------------------------------
    # Ollama
    # -------------------------------------------------------------------------
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:1.5b-instruct"
    OLLAMA_FAST_MODEL: str = "qwen2.5:1.5b-instruct"
    OLLAMA_TEMPERATURE: float = 0.0
    OLLAMA_NUM_CTX: int = 8192
    OLLAMA_TIMEOUT: int = 180

    # -------------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------------
    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    EMBEDDING_DIMENSION: int = 768
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_QUERY_PREFIX: str = (
        "Represent this sentence for searching relevant passages:"
    )
    EMBEDDING_DEVICE: str = "cpu"

    # -------------------------------------------------------------------------
    # Reranking
    # -------------------------------------------------------------------------
    RERANKER_MODEL: str = "BAAI/bge-reranker-base"
    RERANKER_ENABLED: bool = True
    RERANKER_DEVICE: str = "cpu"

    # -------------------------------------------------------------------------
    # Chunking
    # -------------------------------------------------------------------------
    CHUNK_MAX_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64
    CHUNK_MIN_CHARS: int = 80

    # -------------------------------------------------------------------------
    # Retrieval
    # -------------------------------------------------------------------------
    VECTOR_TOP_K: int = 25
    KEYWORD_TOP_K: int = 25
    RRF_K: int = 60
    RERANK_CANDIDATES: int = 20
    FINAL_TOP_K: int = 6
    RERANK_SCORE_THRESHOLD: float = 0.30
    MIN_CONFIDENCE_THRESHOLD: float = 0.35

    # -------------------------------------------------------------------------
    # Guardrails
    # -------------------------------------------------------------------------
    RELEVANCE_GATE_ENABLED: bool = True
    GROUNDING_CHECK_ENABLED: bool = False

    # -------------------------------------------------------------------------
    # Chat
    # -------------------------------------------------------------------------
    CHAT_HISTORY_WINDOW: int = 10
    CHAT_HISTORY_LIMIT: int = 10
    MAX_QUESTION_LENGTH: int = 2000

    # -------------------------------------------------------------------------
    # Storage
    # -------------------------------------------------------------------------
    UPLOAD_DIR: str = "uploads"
    KNOWLEDGE_BASE_DIR: str = "knowledge_base"
    MAX_UPLOAD_SIZE_MB: int = 50

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_LOGIN: str = "10/minute"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        case_sensitive=True,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Derived paths
    # -------------------------------------------------------------------------
    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def log_path(self) -> Path:
        p = BASE_DIR / self.LOG_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def upload_path(self) -> Path:
        p = BASE_DIR / self.UPLOAD_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def knowledge_base_path(self) -> Path:
        p = BASE_DIR / self.KNOWLEDGE_BASE_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------
    @field_validator("SECRET_KEY")
    @classmethod
    def _reject_placeholder_secret(cls, v: str) -> str:
        if len(v) < 32 or "CHANGE" in v.upper():
            raise ValueError(
                "SECRET_KEY is a placeholder or too short. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_psycopg_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DATABASE_URL must use the psycopg3 driver: "
                "postgresql+psycopg://user:pass@host:port/db"
            )
        return v

    @model_validator(mode="after")
    def _production_safety_checks(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if self.DEBUG:
                raise ValueError("DEBUG must be False in production.")
            if "*" in self.cors_origins_list:
                raise ValueError("CORS_ORIGINS cannot be '*' in production.")
        if self.FINAL_TOP_K > self.RERANK_CANDIDATES:
            raise ValueError("FINAL_TOP_K cannot exceed RERANK_CANDIDATES.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
