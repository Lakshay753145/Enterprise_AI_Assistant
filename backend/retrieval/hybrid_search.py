"""Hybrid retrieval: dense vectors + keyword full-text, fused with RRF.

Why both. Semantic search finds "how do I claim travel expenses" against a
passage titled "Reimbursement of Official Travel" - lexical search never
would. Lexical search finds "AS9100 Rev D clause 8.5.1" exactly - semantic
search blurs identifiers into a general neighbourhood and loses the digits.
Aerolloy's corpus is full of both prose policy and hard identifiers (alloy
grades, spec numbers, PO formats), so neither retriever alone is sufficient.

Fusion uses Reciprocal Rank Fusion rather than score normalisation. Cosine
similarity and ts_rank_cd are not on comparable scales and their distributions
shift per query, so any attempt to weight the raw scores needs constant
retuning. RRF only looks at *rank*, which is stable.

**Every** query in this module carries an explicit ``department = :department``
predicate. That is on top of RLS, not instead of it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.config import settings
from backend.core.logging_config import app_logger
from backend.embeddings.embedder import get_embedder
from backend.security.isolation import assert_dicts_in_scope


@dataclass
class RetrievedChunk:
    """A candidate passage plus everything needed to score and cite it."""

    chunk_id: int
    document_id: int
    department: str
    content: str
    heading: str | None
    section_path: str | None
    page_number: int | None
    document_name: str
    document_title: str | None

    vector_score: float = 0.0
    keyword_score: float = 0.0
    fusion_score: float = 0.0
    rerank_score: float | None = None

    vector_rank: int | None = None
    keyword_rank: int | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def final_score(self) -> float:
        """Reranker score when available, else the fusion score."""
        return self.rerank_score if self.rerank_score is not None else self.fusion_score

    @property
    def display_content(self) -> str:
        """Body text without the heading breadcrumb prefix we added at index time."""
        raw = self.metadata.get("raw_content")
        return raw if raw else self.content

    @property
    def source_label(self) -> str:
        parts = [self.document_title or self.document_name]
        if self.page_number:
            parts.append(f"p. {self.page_number}")
        if self.heading:
            parts.append(self.heading)
        return " - ".join(parts)

    def to_citation(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document": self.document_title or self.document_name,
            "filename": self.document_name,
            "page": self.page_number,
            "heading": self.heading,
            "section": self.section_path,
            "snippet": _snippet(self.display_content),
            "score": round(self.final_score, 4),
        }


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    timings_ms: dict[str, float]
    vector_hits: int = 0
    keyword_hits: int = 0
    fused_hits: int = 0

    @property
    def top_score(self) -> float:
        return max((c.final_score for c in self.chunks), default=0.0)

    @property
    def is_empty(self) -> bool:
        return not self.chunks


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
# Note the leading `department = :department` in every WHERE clause. The
# composite index ix_chunks_department_doc and the HNSW index both work with
# it, and it means no query shape exists here that could return foreign rows.

_VECTOR_SQL = text(
    """
    SELECT
        c.id                AS chunk_id,
        c.document_id,
        c.department,
        c.content,
        c.heading,
        c.section_path,
        c.page_number,
        c.chunk_metadata,
        d.original_filename AS document_name,
        d.title             AS document_title,
        1 - (c.embedding <=> CAST(:query_vector AS vector)) AS score
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.department = :department
      AND d.status = 'completed'
    ORDER BY c.embedding <=> CAST(:query_vector AS vector)
    LIMIT :limit
    """
)

_KEYWORD_SQL = text(
    """
    WITH q AS (
        SELECT websearch_to_tsquery('english', :query) AS tsq
    )
    SELECT
        c.id                AS chunk_id,
        c.document_id,
        c.department,
        c.content,
        c.heading,
        c.section_path,
        c.page_number,
        c.chunk_metadata,
        d.original_filename AS document_name,
        d.title             AS document_title,
        ts_rank_cd(c.content_tsv, q.tsq, 32) AS score
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN q
    WHERE c.department = :department
      AND d.status = 'completed'
      AND c.content_tsv @@ q.tsq
    ORDER BY score DESC
    LIMIT :limit
    """
)

# Fuzzy fallback for identifiers the user mistyped ("AS-9100" vs "AS9100",
# "IN718" vs "Inconel 718"). Only runs when exact keyword search came back
# empty. word_similarity matches the query against the best-matching *span* of
# a chunk rather than the whole chunk, which is what we want here - a spec code
# is a few characters inside a long passage.
_TRIGRAM_SQL = text(
    """
    SELECT
        c.id                AS chunk_id,
        c.document_id,
        c.department,
        c.content,
        c.heading,
        c.section_path,
        c.page_number,
        c.chunk_metadata,
        d.original_filename AS document_name,
        d.title             AS document_title,
        word_similarity(:query, c.content) AS score
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.department = :department
      AND d.status = 'completed'
      AND word_similarity(:query, c.content) > 0.45
    ORDER BY score DESC
    LIMIT :limit
    """
)


# ---------------------------------------------------------------------------
# Individual retrievers
# ---------------------------------------------------------------------------

async def vector_search(
    session: AsyncSession,
    *,
    query_vector: list[float],
    department: str,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    rows = (
        await session.execute(
            _VECTOR_SQL,
            {
                "query_vector": _vector_literal(query_vector),
                "department": department,
                "limit": limit or settings.VECTOR_TOP_K,
            },
        )
    ).mappings().all()

    return [_row_to_chunk(row, vector_score=float(row["score"])) for row in rows]


async def keyword_search(
    session: AsyncSession,
    *,
    query: str,
    department: str,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    limit = limit or settings.KEYWORD_TOP_K
    cleaned = _clean_for_fts(query)
    if not cleaned:
        return []

    rows = (
        await session.execute(
            _KEYWORD_SQL,
            {"query": cleaned, "department": department, "limit": limit},
        )
    ).mappings().all()

    if not rows and _looks_like_identifier(query):
        app_logger.debug(f"Keyword search empty; trying trigram for {query!r}")
        rows = (
            await session.execute(
                _TRIGRAM_SQL,
                {"query": query.strip(), "department": department, "limit": limit},
            )
        ).mappings().all()

    return [_row_to_chunk(row, keyword_score=float(row["score"])) for row in rows]


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]],
    *,
    weights: list[float] | None = None,
    k: int | None = None,
) -> list[RetrievedChunk]:
    """Fuse ranked lists.

    score(d) = sum over lists of  weight_i / (k + rank_i(d))

    A document appearing at rank 3 in both lists beats one that is rank 1 in
    only one - which is exactly the "agreement is evidence" behaviour we want
    from a hybrid retriever.
    """
    k = k or settings.RRF_K
    weights = weights or [1.0] * len(ranked_lists)

    merged: dict[int, RetrievedChunk] = {}
    scores: dict[int, float] = {}

    for list_index, (chunks, weight) in enumerate(zip(ranked_lists, weights)):
        for rank, chunk in enumerate(chunks, start=1):
            existing = merged.get(chunk.chunk_id)
            if existing is None:
                merged[chunk.chunk_id] = chunk
                existing = chunk
            else:
                # Carry across whichever score this list contributed.
                existing.vector_score = max(existing.vector_score, chunk.vector_score)
                existing.keyword_score = max(
                    existing.keyword_score, chunk.keyword_score
                )

            if list_index == 0:
                existing.vector_rank = rank
            else:
                existing.keyword_rank = rank

            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + weight / (
                k + rank
            )

    for chunk_id, score in scores.items():
        merged[chunk_id].fusion_score = score

    return sorted(merged.values(), key=lambda c: c.fusion_score, reverse=True)


# ---------------------------------------------------------------------------
# Orchestrated hybrid search
# ---------------------------------------------------------------------------

async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    department: str,
    role: str = "user",
    username: str | None = None,
    query_variants: list[str] | None = None,
    limit: int | None = None,
) -> RetrievalResult:
    """Run both retrievers and fuse.

    Args:
        query: the search query (normally the rewritten, technical form).
        query_variants: extra phrasings from the rewriter. Their vector hits
            are folded in, which lifts recall on questions where the user's
            vocabulary and the document's vocabulary do not overlap at all.
    """
    timings: dict[str, float] = {}
    limit = limit or settings.RERANK_CANDIDATES

    embed_started = time.perf_counter()
    embedder = get_embedder()
    all_queries = [query] + [v for v in (query_variants or []) if v.strip()]
    vectors = await embedder.aembed_queries(all_queries)
    timings["embed_ms"] = _ms(embed_started)

    search_started = time.perf_counter()

    vector_lists: list[list[RetrievedChunk]] = []
    for vector in vectors:
        vector_lists.append(
            await vector_search(
                session,
                query_vector=vector,
                department=department,
                limit=settings.VECTOR_TOP_K,
            )
        )

    keyword_hits = await keyword_search(
        session, query=query, department=department, limit=settings.KEYWORD_TOP_K
    )
    timings["search_ms"] = _ms(search_started)

    # Merge the variant vector lists first (they are the same retriever, so
    # fusing them against each other with equal weight is just recall union),
    # then fuse the combined dense list against the lexical list.
    fuse_started = time.perf_counter()
    dense = (
        vector_lists[0]
        if len(vector_lists) == 1
        else reciprocal_rank_fusion(vector_lists)
    )
    fused = reciprocal_rank_fusion([dense, keyword_hits], weights=[1.0, 0.85])
    timings["fusion_ms"] = _ms(fuse_started)

    candidates = fused[:limit]

    # Layer 4 of isolation: nothing leaves this function unchecked.
    assert_dicts_in_scope(
        [{"department": c.department, "id": c.chunk_id} for c in candidates],
        department=department,
        role=role,
        username=username,
        resource="retrieved_chunks",
    )

    app_logger.debug(
        f"Hybrid search [{department}] q={query[:60]!r} -> "
        f"{len(dense)} dense + {len(keyword_hits)} lexical = {len(fused)} fused "
        f"({timings['search_ms']:.0f} ms)"
    )

    return RetrievalResult(
        chunks=candidates,
        timings_ms=timings,
        vector_hits=len(dense),
        keyword_hits=len(keyword_hits),
        fused_hits=len(fused),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_chunk(
    row, *, vector_score: float = 0.0, keyword_score: float = 0.0
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        department=row["department"],
        content=row["content"],
        heading=row["heading"],
        section_path=row["section_path"],
        page_number=row["page_number"],
        document_name=row["document_name"],
        document_title=row["document_title"],
        vector_score=vector_score,
        keyword_score=keyword_score,
        metadata=dict(row["chunk_metadata"] or {}),
    )


def _vector_literal(vector: list[float]) -> str:
    """pgvector accepts a bracketed literal; this avoids needing the type
    adapter to be registered on every pooled connection."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def _clean_for_fts(query: str) -> str:
    """websearch_to_tsquery is tolerant, but stray operators still confuse it."""
    cleaned = re.sub(r"[\x00\\]", " ", query).strip()
    return cleaned[:1000]


_IDENTIFIER_RE = re.compile(r"[A-Za-z]{1,6}[-_ ]?\d{2,}|\d{2,}[-_ ]?[A-Za-z]{1,6}")


def _looks_like_identifier(query: str) -> bool:
    """True for things like 'AS9100', 'IN-718', 'PO 4500123', 'ISO 9001'."""
    return bool(_IDENTIFIER_RE.search(query))


def _snippet(text_value: str, length: int = 320) -> str:
    collapsed = " ".join(text_value.split())
    if len(collapsed) <= length:
        return collapsed
    return collapsed[:length].rsplit(" ", 1)[0] + "..."


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
