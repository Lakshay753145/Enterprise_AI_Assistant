"""Document parsing with Docling.

Why Docling rather than pypdf/PyMuPDF: Aerolloy's knowledge base is engineering
documentation - process routings, inspection plans, spec sheets. The meaning
lives in the *tables* and the *heading hierarchy*, and a naive text extractor
flattens both into an unusable soup of numbers. Docling runs layout analysis
and table-structure recognition, so a 12-column inspection table survives as a
table and a numbered clause keeps its clause number.

The converter is expensive to construct (it loads layout + table models), so
one instance is built lazily and reused for the life of the process.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.exceptions import IngestionError, UnsupportedFileTypeError
from backend.core.logging_config import ingestion_logger

_converter = None
_converter_lock = threading.Lock()


@dataclass
class ParsedDocument:
    """The normalised output of a parse, ready for chunking."""

    #: The DoclingDocument itself - handed to HybridChunker.
    docling_document: Any
    markdown: str
    title: str | None
    page_count: int
    file_hash: str
    size_bytes: int
    parse_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.markdown)


def compute_file_hash(path: Path) -> str:
    """SHA-256 of the file bytes, streamed so large PDFs do not blow memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_converter():
    """Construct the Docling converter with accuracy-biased options."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()

    # OCR: scanned drawings and photocopied work instructions are common in a
    # manufacturing archive. Slower, but a page we cannot read is a page the
    # chatbot will confidently claim does not exist.
    options.do_ocr = True

    # Table structure recognition - the single biggest accuracy win on this
    # corpus. ACCURATE mode over FAST: ingestion is a background job run once
    # per document, so seconds there buy correctness on every later query.
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True
    try:
        from docling.datamodel.pipeline_options import TableFormerMode

        options.table_structure_options.mode = TableFormerMode.ACCURATE
    except ImportError:  # older docling - default mode is fine
        pass

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    return converter


def get_converter():
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                started = time.perf_counter()
                ingestion_logger.info("Loading Docling converter (layout + tables)...")
                _converter = _build_converter()
                ingestion_logger.info(
                    f"Docling converter ready in {time.perf_counter() - started:.1f}s"
                )
    return _converter


def warmup_converter() -> None:
    """Build the converter ahead of the first upload."""
    try:
        get_converter()
    except Exception as exc:  # pragma: no cover - non-fatal
        ingestion_logger.warning(f"Docling warmup failed (will retry on use): {exc}")


def parse_document(path: Path) -> ParsedDocument:
    """Parse a file into a :class:`ParsedDocument`.

    Tries Docling first (layout + table aware). If Docling fails (e.g. on
    Windows without a C++ compiler), falls back to PyPDF which is lightweight
    and works everywhere.

    Raises :class:`IngestionError` on anything that leaves us without usable
    text - an empty result is treated as a failure rather than an empty
    document, because silently ingesting zero chunks looks like success and
    then the chatbot cannot answer anything about that file.
    """
    from backend.core.constants import SUPPORTED_EXTENSIONS

    path = Path(path)
    if not path.is_file():
        raise IngestionError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"{suffix or 'file'} is not supported. Supported types: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS))
        )

    size_bytes = path.stat().st_size
    file_hash = compute_file_hash(path)

    started = time.perf_counter()
    ingestion_logger.info(f"Parsing {path.name} ({size_bytes / 1_048_576:.1f} MB)...")

    # --- Try Docling first (best quality) ---
    try:
        result = get_converter().convert(str(path))
        document = getattr(result, "document", None)
        if document is None:
            raise IngestionError(f"Docling returned no document for {path.name}.")

        markdown = document.export_to_markdown()
        if not markdown or not markdown.strip():
            raise IngestionError("Docling produced empty text.")

        elapsed = time.perf_counter() - started
        page_count = _count_pages(document)
        title = _extract_title_from_docling(document, markdown, path)

        ingestion_logger.info(
            f"Parsed {path.name} with Docling: {page_count} pages, "
            f"{len(markdown):,} chars, {elapsed:.1f}s"
        )

        return ParsedDocument(
            docling_document=document,
            markdown=markdown,
            title=title,
            page_count=page_count,
            file_hash=file_hash,
            size_bytes=size_bytes,
            parse_seconds=round(elapsed, 3),
            metadata={
                "source_filename": path.name,
                "parser": "docling",
                "ocr": True,
                "table_structure": True,
            },
        )

    except Exception as docling_exc:
        ingestion_logger.warning(
            f"Docling failed on {path.name}: {docling_exc}. "
            f"Falling back to PyPDF lightweight parser..."
        )

    # --- Fallback: PyPDF (works on Windows without C++ compiler) ---
    return _parse_with_pypdf(path, file_hash, size_bytes, started)


def _parse_with_pypdf(
    path: Path,
    file_hash: str,
    size_bytes: int,
    started: float,
) -> ParsedDocument:
    """Lightweight PDF parser using pypdf. No torch, no C++ compiler needed."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise IngestionError(
            "Neither Docling nor pypdf could parse this document. "
            "Install pypdf: pip install pypdf"
        )

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise IngestionError(f"Could not open {path.name}: {exc}") from exc

    page_count = len(reader.pages)
    pages_text: list[str] = []

    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            pages_text.append(text.strip())

    full_text = "\n\n".join(pages_text)

    if not full_text.strip():
        raise IngestionError(
            f"{path.name} produced no extractable text via PyPDF. "
            f"If this is a scanned/image-only PDF, it requires OCR support."
        )

    # Convert to simple markdown
    markdown = _text_to_markdown(full_text, path)

    elapsed = time.perf_counter() - started
    title = _extract_title_from_text(markdown, path)

    ingestion_logger.info(
        f"Parsed {path.name} with PyPDF: {page_count} pages, "
        f"{len(markdown):,} chars, {elapsed:.1f}s"
    )

    # Create a lightweight placeholder for docling_document so the chunker
    # falls back to its markdown path (which is what we want here).
    return ParsedDocument(
        docling_document=None,
        markdown=markdown,
        title=title,
        page_count=page_count,
        file_hash=file_hash,
        size_bytes=size_bytes,
        parse_seconds=round(elapsed, 3),
        metadata={
            "source_filename": path.name,
            "parser": "pypdf_fallback",
            "ocr": False,
            "table_structure": False,
        },
    )


def _text_to_markdown(text: str, path: Path) -> str:
    """Convert raw extracted text to basic markdown structure."""
    lines = text.splitlines()
    md_lines: list[str] = []
    title_added = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            md_lines.append("")
            continue

        # Heuristic: short ALL-CAPS lines are likely headings
        if (
            stripped.isupper()
            and len(stripped) < 120
            and len(stripped.split()) <= 12
        ):
            if not title_added:
                md_lines.append(f"# {stripped.title()}")
                title_added = True
            else:
                md_lines.append(f"## {stripped.title()}")
        else:
            md_lines.append(stripped)

    return "\n".join(md_lines)


def _count_pages(document: Any) -> int:
    pages = getattr(document, "pages", None)
    if pages is None:
        return 0
    try:
        return len(pages)
    except TypeError:
        return 0


def _extract_title_from_docling(document: Any, markdown: str, path: Path) -> str | None:
    """Prefer the document's own title, then its first H1, then the filename."""
    name = getattr(document, "name", None)
    if name and str(name).strip() and str(name).strip().lower() != path.stem.lower():
        return str(name).strip()[:500]

    return _extract_title_from_text(markdown, path)


def _extract_title_from_text(markdown: str, path: Path) -> str | None:
    """Extract title from markdown text: first H1, or cleaned filename."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            candidate = stripped[2:].strip()
            if candidate:
                return candidate[:500]
        if stripped and not stripped.startswith("#"):
            break  # body started; no leading H1

    return path.stem.replace("_", " ").replace("-", " ").strip()[:500] or None
