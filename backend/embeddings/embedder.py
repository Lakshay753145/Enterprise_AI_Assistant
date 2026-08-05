"""Dense embedding service.

One process-wide model instance, loaded lazily on first use so importing this
module (which the CLI, the API, and alembic all do) does not pull ~450 MB of
weights into memory for a request that will never embed anything.

BGE asymmetry matters and is easy to get wrong: queries must be prefixed with
an instruction, documents must **not** be. Getting this backwards silently
degrades recall by several points, which is why the two paths are separate
methods rather than one method with a flag the caller might forget.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Sequence

from backend.config.config import settings
from backend.core.logging_config import app_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class EmbeddingService:
    """Thread-safe singleton wrapper around a SentenceTransformer."""

    _instance: "EmbeddingService | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model: "SentenceTransformer | None" = None
        self._model_lock = threading.Lock()
        self.model_name = settings.EMBEDDING_MODEL
        self.dimension = settings.EMBEDDING_DIMENSION
        self.device = settings.EMBEDDING_DEVICE
        self.query_prefix = settings.EMBEDDING_QUERY_PREFIX

    @classmethod
    def instance(cls) -> "EmbeddingService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- model lifecycle ---------------------------------------------------
    @property
    def model(self) -> "SentenceTransformer":
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = self._load()
        return self._model

    def _load(self) -> "SentenceTransformer":
        from sentence_transformers import SentenceTransformer

        started = time.perf_counter()
        app_logger.info(
            f"Loading embedding model {self.model_name} on {self.device}..."
        )
        model = SentenceTransformer(self.model_name, device=self.device)

        actual_dim = model.get_sentence_embedding_dimension()
        if actual_dim != self.dimension:
            # Fail hard: a mismatch means every vector written would be
            # rejected by the DB column, or worse, silently truncated.
            raise RuntimeError(
                f"EMBEDDING_DIMENSION is {self.dimension} but {self.model_name} "
                f"produces {actual_dim}-dimensional vectors. Update .env and "
                f"re-run the migration - existing embeddings must be rebuilt."
            )

        app_logger.info(
            f"Embedding model ready in {time.perf_counter() - started:.1f}s "
            f"(dim={actual_dim})"
        )
        return model

    def warmup(self) -> None:
        """Force model load + one forward pass at startup.

        Without this the first user of the day waits ~20 s for weights to load
        mid-question, and blames the chatbot.
        """
        self.embed_query("warmup")

    # -- encoding ----------------------------------------------------------
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for storage. No instruction prefix."""
        if not texts:
            return []

        vectors = self.model.encode(
            list(texts),
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,  # cosine distance == dot product
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query. Instruction prefix applied."""
        prefixed = f"{self.query_prefix} {text}" if self.query_prefix else text
        vector = self.model.encode(
            prefixed,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vector.tolist()

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch query embedding - used when the rewriter emits variants."""
        if not texts:
            return []
        prefixed = [
            f"{self.query_prefix} {t}" if self.query_prefix else t for t in texts
        ]
        vectors = self.model.encode(
            prefixed,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    # -- async wrappers ----------------------------------------------------
    # Encoding is CPU-bound and holds the GIL only partially (torch releases it
    # in the heavy kernels), so a thread offload keeps the event loop free.
    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_queries, texts)

    async def aembed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)


def get_embedder() -> EmbeddingService:
    return EmbeddingService.instance()
