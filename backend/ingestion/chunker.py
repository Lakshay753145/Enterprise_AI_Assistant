"""Chunking strategy.

Primary strategy: **Docling HybridChunker**. It is hierarchy-aware (splits on
the document's real structure - sections, list items, table rows - never
mid-table) *and* tokenizer-aware (packs each chunk to just under the embedding
model's token budget, then merges undersized neighbours that share a heading).

On top of Docling's own output we do two things:

1. **Contextualisation.** Each chunk is prefixed with its heading breadcrumb,
   e.g. ``Quality Manual > 7.3 Incoming Inspection``. A chunk that says "the
   limit is 0.05 mm" is useless in isolation; with its breadcrumb it is
   retrievable *and* the LLM can cite it correctly. This is the single highest
   -leverage retrieval improvement in the pipeline.

2. **Quality filtering.** Fragments that are pure page furniture (page numbers,
   repeated headers/footers) are dropped so they cannot dilute the index.

If Docling's chunker is unavailable, a structure-aware markdown splitter is
used instead so ingestion degrades rather than fails.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.config.config import settings
from backend.core.logging_config import ingestion_logger

_chunker = None
_chunker_lock = threading.Lock()
_tokenizer = None


@dataclass
class Chunk:
    """One retrievable passage plus the provenance needed to cite it."""

    index: int
    #: Text sent to the embedder: breadcrumb + body.
    content: str
    #: Body only - what gets shown to the user as a quote.
    raw_content: str
    heading: str | None = None
    section_path: str | None = None
    page_number: int | None = None
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tokenizer / chunker construction
# ---------------------------------------------------------------------------

def _get_tokenizer():
    """The *embedding model's* tokenizer, so token budgets are exact."""
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(settings.EMBEDDING_MODEL)
    return _tokenizer


def _get_hybrid_chunker():
    global _chunker
    if _chunker is None:
        with _chunker_lock:
            if _chunker is None:
                from docling.chunking import HybridChunker

                tokenizer = _get_tokenizer()
                try:
                    # docling >= 2.14 wraps the tokenizer
                    from docling_core.transforms.chunker.tokenizer.huggingface import (
                        HuggingFaceTokenizer,
                    )

                    wrapped = HuggingFaceTokenizer(
                        tokenizer=tokenizer, max_tokens=settings.CHUNK_MAX_TOKENS
                    )
                    _chunker = HybridChunker(tokenizer=wrapped, merge_peers=True)
                except ImportError:
                    _chunker = HybridChunker(
                        tokenizer=tokenizer,
                        max_tokens=settings.CHUNK_MAX_TOKENS,
                        merge_peers=True,
                    )
                ingestion_logger.info(
                    f"HybridChunker ready (max_tokens={settings.CHUNK_MAX_TOKENS})"
                )
    return _chunker


def count_tokens(text: str) -> int:
    try:
        return len(_get_tokenizer().encode(text, add_special_tokens=False))
    except Exception:
        # ~4 characters per token is close enough for a metrics field.
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def chunk_document(parsed) -> list[Chunk]:
    """Chunk a :class:`~backend.ingestion.docling_parser.ParsedDocument`."""
    # If docling_document is None, we used the PyPDF fallback parser.
    # Skip the Docling HybridChunker and go straight to markdown splitting.
    if parsed.docling_document is not None:
        try:
            chunks = _chunk_with_docling(parsed.docling_document)
            if chunks:
                ingestion_logger.info(f"HybridChunker produced {len(chunks)} chunks")
                return chunks
            ingestion_logger.warning(
                "HybridChunker produced nothing; falling back to markdown splitting"
            )
        except Exception as exc:
            ingestion_logger.warning(
                f"HybridChunker unavailable ({exc}); falling back to markdown splitting"
            )
    else:
        ingestion_logger.info("Using markdown splitter (PyPDF fallback parse)")

    chunks = _chunk_markdown(parsed.markdown, title=parsed.title)
    ingestion_logger.info(f"Markdown splitter produced {len(chunks)} chunks")
    return chunks


# ---------------------------------------------------------------------------
# Docling path
# ---------------------------------------------------------------------------

def _chunk_with_docling(document: Any) -> list[Chunk]:
    chunker = _get_hybrid_chunker()
    results: list[Chunk] = []
    index = 0

    for docling_chunk in chunker.chunk(dl_doc=document):
        raw = (getattr(docling_chunk, "text", "") or "").strip()
        if not _is_useful(raw):
            continue

        headings = _extract_headings(docling_chunk)
        page = _extract_page(docling_chunk)

        # Docling's own contextualiser prepends headings; prefer it, because it
        # knows about table captions and list nesting that we do not.
        try:
            contextualised = chunker.contextualize(chunk=docling_chunk).strip()
        except Exception:
            contextualised = _prepend_breadcrumb(raw, headings)

        section_path = " > ".join(headings) if headings else None

        results.append(
            Chunk(
                index=index,
                content=contextualised or raw,
                raw_content=raw,
                heading=headings[-1] if headings else None,
                section_path=section_path,
                page_number=page,
                token_count=count_tokens(contextualised or raw),
                metadata={
                    "chunker": "docling_hybrid",
                    "headings": headings,
                    "content_type": _classify(raw),
                },
            )
        )
        index += 1

    return results


def _extract_headings(docling_chunk: Any) -> list[str]:
    meta = getattr(docling_chunk, "meta", None)
    if meta is None:
        return []
    headings = getattr(meta, "headings", None) or []
    return [str(h).strip() for h in headings if str(h).strip()]


def _extract_page(docling_chunk: Any) -> int | None:
    """First page this chunk's content appears on, via Docling provenance."""
    meta = getattr(docling_chunk, "meta", None)
    if meta is None:
        return None

    for item in getattr(meta, "doc_items", None) or []:
        for prov in getattr(item, "prov", None) or []:
            page = getattr(prov, "page_no", None)
            if page is not None:
                return int(page)
    return None


# ---------------------------------------------------------------------------
# Fallback markdown path
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _chunk_markdown(markdown: str, *, title: str | None = None) -> list[Chunk]:
    """Structure-aware markdown splitter.

    Walks the heading hierarchy, accumulates text under each heading, and packs
    it into token-bounded chunks with overlap. Tables (``|`` rows) are kept
    whole where they fit, because half a table is worse than no table.
    """
    lines = markdown.splitlines()
    breadcrumb: list[str] = [title] if title else []
    stack: list[tuple[int, str]] = []

    sections: list[tuple[list[str], list[str]]] = []
    current_lines: list[str] = []
    current_path: list[str] = list(breadcrumb)

    for line in lines:
        match = _HEADING_RE.match(line.strip())
        if match:
            if current_lines:
                sections.append((list(current_path), current_lines))
                current_lines = []
            level = len(match.group(1))
            heading = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_path = list(breadcrumb) + [h for _, h in stack]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((list(current_path), current_lines))

    chunks: list[Chunk] = []
    index = 0

    for path, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not _is_useful(body):
            continue

        breadcrumb_text = " > ".join(path) if path else ""
        for piece in _pack(body):
            content = (
                f"{breadcrumb_text}\n\n{piece}" if breadcrumb_text else piece
            )
            chunks.append(
                Chunk(
                    index=index,
                    content=content,
                    raw_content=piece,
                    heading=path[-1] if path else None,
                    section_path=breadcrumb_text or None,
                    page_number=None,
                    token_count=count_tokens(content),
                    metadata={
                        "chunker": "markdown_fallback",
                        "headings": path,
                        "content_type": _classify(piece),
                    },
                )
            )
            index += 1

    return chunks


def _pack(text: str) -> list[str]:
    """Split text into token-bounded pieces on paragraph boundaries."""
    max_tokens = settings.CHUNK_MAX_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS

    if count_tokens(text) <= max_tokens:
        return [text]

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    for paragraph in paragraphs:
        tokens = count_tokens(paragraph)

        # A single oversized paragraph (usually a wide table) gets split on
        # line boundaries rather than mid-row.
        if tokens > max_tokens:
            if buffer:
                pieces.append("\n\n".join(buffer))
                buffer, buffer_tokens = [], 0
            pieces.extend(_split_long_block(paragraph, max_tokens))
            continue

        if buffer_tokens + tokens > max_tokens and buffer:
            pieces.append("\n\n".join(buffer))
            # Carry the tail of the previous chunk forward so a sentence that
            # straddles the boundary is still retrievable from both sides.
            tail, tail_tokens = [], 0
            for prior in reversed(buffer):
                prior_tokens = count_tokens(prior)
                if tail_tokens + prior_tokens > overlap:
                    break
                tail.insert(0, prior)
                tail_tokens += prior_tokens
            buffer, buffer_tokens = tail, tail_tokens

        buffer.append(paragraph)
        buffer_tokens += tokens

    if buffer:
        pieces.append("\n\n".join(buffer))

    return [p for p in pieces if p.strip()]


def _split_long_block(block: str, max_tokens: int) -> list[str]:
    lines = block.splitlines()
    pieces: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    # Markdown table? Repeat the header row on every piece so each fragment is
    # self-describing.
    header: list[str] = []
    if len(lines) >= 2 and lines[0].lstrip().startswith("|"):
        header = lines[:2]

    for line in lines:
        tokens = count_tokens(line)
        if buffer_tokens + tokens > max_tokens and buffer:
            pieces.append("\n".join(buffer))
            buffer = list(header)
            buffer_tokens = sum(count_tokens(h) for h in header)
        buffer.append(line)
        buffer_tokens += tokens

    if buffer:
        pieces.append("\n".join(buffer))
    return pieces


# ---------------------------------------------------------------------------
# Quality filters
# ---------------------------------------------------------------------------

_PAGE_FURNITURE = re.compile(
    r"^\s*(page\s*\d+(\s*(of|/)\s*\d+)?|\d+|[-_=*\s]+|confidential|"
    r"internal\s+use\s+only)\s*$",
    re.IGNORECASE,
)


def _is_useful(text: str) -> bool:
    """Reject fragments that would only add noise to the index."""
    stripped = text.strip()
    if len(stripped) < settings.CHUNK_MIN_CHARS:
        return False
    if _PAGE_FURNITURE.match(stripped):
        return False
    # Needs some actual words, not just punctuation and figures.
    if len(re.findall(r"[A-Za-z]{3,}", stripped)) < 3:
        return False
    return True


def _classify(text: str) -> str:
    """Coarse content type - surfaced in the UI and useful for debugging."""
    lines = text.strip().splitlines()
    if not lines:
        return "text"
    pipe_lines = sum(1 for line in lines if line.strip().startswith("|"))
    if pipe_lines >= 2 and pipe_lines >= len(lines) * 0.5:
        return "table"
    bullet_lines = sum(
        1 for line in lines if re.match(r"^\s*([-*+]|\d+[.)])\s+", line)
    )
    if bullet_lines >= max(2, len(lines) * 0.5):
        return "list"
    return "text"


def _prepend_breadcrumb(text: str, headings: list[str]) -> str:
    if not headings:
        return text
    return f"{' > '.join(headings)}\n\n{text}"
