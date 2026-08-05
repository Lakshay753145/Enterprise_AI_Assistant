"""Cross-encoder reranking.

Bi-encoders (what the vector index uses) embed the query and the passage
*independently*, so they can only measure whether two texts occupy a similar
region of meaning-space. A cross-encoder reads the query and the passage
together in one forward pass and scores actual relevance. It is far too slow to
run over a whole corpus, and exactly right for reordering ~20 candidates.

This is the step that turns "these 20 passages are about heat treatment" into
"this passage answers the question about solution-annealing temperature".

It also does the gating. A cross-encoder score is calibrated enough to act on:
if the best passage scores below the threshold, the honest answer is "not in
the knowledge base", and the graph refuses rather than handing the LLM weak
context to hallucinate around.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from backend.config.config import settings
from backend.core.logging_config import app_logger
from backend.retrieval.hybrid_search import RetrievedChunk

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


@dataclass
class RerankResult:
    chunks: list[RetrievedChunk]
    top_score: float
    took_ms: float
    #: True when nothing cleared MIN_CONFIDENCE_THRESHOLD.
    below_confidence: bool
    considered: int
    kept: int


class RerankerService:
    """Process-wide cross-encoder, loaded lazily."""

    _instance: "RerankerService | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model: "CrossEncoder | None" = None
        self._model_lock = threading.Lock()
        self.model_name = settings.RERANKER_MODEL
        self.device = settings.RERANKER_DEVICE

    @classmethod
    def instance(cls) -> "RerankerService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def model(self) -> "CrossEncoder":
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    started = time.perf_counter()
                    app_logger.info(
                        f"Loading reranker {self.model_name} on {self.device}..."
                    )
                    self._model = CrossEncoder(
                        self.model_name, device=self.device, max_length=512
                    )
                    app_logger.info(
                        f"Reranker ready in {time.perf_counter() - started:.1f}s"
                    )
        return self._model

    def warmup(self) -> None:
        try:
            self.model.predict([("warmup query", "warmup passage")])
        except Exception as exc:  # pragma: no cover
            app_logger.warning(f"Reranker warmup failed: {exc}")

    # -- scoring -----------------------------------------------------------
    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        raw = self.model.predict(
            [(query, passage) for passage in passages],
            batch_size=16,
            show_progress_bar=False,
        )
        # bge-reranker emits raw logits; squash to 0-1 so the threshold in
        # .env means something stable across model swaps.
        return [_sigmoid(float(value)) for value in raw]

    async def ascore(self, query: str, passages: Sequence[str]) -> list[float]:
        return await asyncio.to_thread(self.score, query, passages)


def get_reranker() -> RerankerService:
    return RerankerService.instance()


async def rerank(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_k: int | None = None,
    score_threshold: float | None = None,
) -> RerankResult:
    """Rerank candidates and keep the best ones above the threshold."""
    top_k = top_k or settings.FINAL_TOP_K
    score_threshold = (
        score_threshold if score_threshold is not None
        else settings.RERANK_SCORE_THRESHOLD
    )
    started = time.perf_counter()

    if not chunks:
        return RerankResult(
            chunks=[],
            top_score=0.0,
            took_ms=0.0,
            below_confidence=True,
            considered=0,
            kept=0,
        )

    if not settings.RERANKER_ENABLED:
        kept = chunks[:top_k]
        top = kept[0].fusion_score if kept else 0.0
        return RerankResult(
            chunks=kept,
            top_score=top,
            took_ms=_ms(started),
            below_confidence=False,  # no calibrated score to gate on
            considered=len(chunks),
            kept=len(kept),
        )

    # Score against the contextualised text (breadcrumb + body): the heading is
    # often what makes a passage's relevance obvious.
    scores = await get_reranker().ascore(query, [c.content for c in chunks])

    for chunk, score in zip(chunks, scores):
        chunk.rerank_score = score

    ordered = sorted(chunks, key=lambda c: c.rerank_score or 0.0, reverse=True)
    top_score = ordered[0].rerank_score or 0.0

    kept = [c for c in ordered if (c.rerank_score or 0.0) >= score_threshold][:top_k]

    # If everything failed the bar but the best is still respectable, keep the
    # single best passage - the grounding check downstream makes the final
    # call. Dropping to zero context here would refuse questions that a
    # slightly-conservative threshold could have answered.
    if not kept and top_score >= settings.MIN_CONFIDENCE_THRESHOLD:
        kept = ordered[:1]

    took = _ms(started)
    app_logger.debug(
        f"Rerank: {len(chunks)} -> {len(kept)} kept, top={top_score:.3f}, "
        f"{took:.0f} ms"
    )

    return RerankResult(
        chunks=kept,
        top_score=top_score,
        took_ms=took,
        below_confidence=top_score < settings.MIN_CONFIDENCE_THRESHOLD,
        considered=len(chunks),
        kept=len(kept),
    )


def _sigmoid(x: float) -> float:
    import math

    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
