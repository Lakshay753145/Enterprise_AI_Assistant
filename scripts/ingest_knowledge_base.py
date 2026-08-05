"""Ingest the knowledge_base/ tree into the vector store.

    python -m scripts.ingest_knowledge_base                    # every department
    python -m scripts.ingest_knowledge_base --department HR
    python -m scripts.ingest_knowledge_base --department HR --replace
    python -m scripts.ingest_knowledge_base --file path/to.pdf --department Finance

Drop each department's PDFs into knowledge_base/<Department>/ first:

    knowledge_base/
      Finance/     ...pdf
      HR/          ...pdf
      IT/          ...pdf
      Production/  ...pdf
      Purchase/    ...pdf

The department a document lands in is taken from its folder (or --department),
never from the document's contents.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config.config import settings  # noqa: E402
from backend.core.constants import Department  # noqa: E402
from backend.core.logging_config import setup_logging  # noqa: E402


def _print_results(department: str, results: list) -> tuple[int, int, int]:
    ok = sum(1 for r in results if r.status == "completed")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")

    print(f"\n  {department}")
    print("  " + "-" * 70)
    if not results:
        print("    (no files found)")
        return ok, skipped, failed

    for result in results:
        if result.status == "completed":
            print(
                f"    OK      {result.filename[:44]:<44} "
                f"{result.chunk_count:>4} chunks  {result.seconds:>6.1f}s"
            )
        elif result.status == "skipped":
            print(f"    SKIP    {result.filename[:44]:<44} already ingested")
        else:
            print(f"    FAILED  {result.filename[:44]:<44} {(result.error or '')[:60]}")

    return ok, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest knowledge base documents.")
    parser.add_argument(
        "--department",
        choices=Department.values(),
        help="Ingest only this department. Omit to do all of them.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Ingest a single file. Requires --department.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Re-ingest files already present (by content hash).",
    )
    parser.add_argument(
        "--user",
        default="system",
        help="Username recorded as the uploader (default: system).",
    )
    args = parser.parse_args()

    setup_logging()

    from backend.ingestion.pipeline import ingest_department_folder, ingest_file

    if args.file and not args.department:
        parser.error("--file requires --department")

    print("=" * 78)
    print(f"  {settings.ORG_NAME} - knowledge base ingestion")
    print(f"  Embedding model : {settings.EMBEDDING_MODEL}")
    print(f"  Chunk budget    : {settings.CHUNK_MAX_TOKENS} tokens")
    print(f"  Source          : {settings.knowledge_base_path}")
    print("=" * 78)
    print("\n  First run downloads the Docling and embedding models. Be patient.\n")

    started = time.perf_counter()
    total_ok = total_skipped = total_failed = 0

    if args.file:
        if not args.file.is_file():
            print(f"\nFile not found: {args.file}", file=sys.stderr)
            return 1
        result = ingest_file(
            args.file,
            department=args.department,
            uploaded_by_username=args.user,
            replace_existing=args.replace,
        )
        ok, skipped, failed = _print_results(args.department, [result])
        total_ok, total_skipped, total_failed = ok, skipped, failed
    else:
        departments = [args.department] if args.department else Department.values()
        for department in departments:
            results = ingest_department_folder(
                department,
                uploaded_by_username=args.user,
                replace_existing=args.replace,
            )
            ok, skipped, failed = _print_results(department, results)
            total_ok += ok
            total_skipped += skipped
            total_failed += failed

    elapsed = time.perf_counter() - started
    print("\n" + "=" * 78)
    print(
        f"  Done in {elapsed:.1f}s | {total_ok} ingested, "
        f"{total_skipped} skipped, {total_failed} failed"
    )
    print("=" * 78 + "\n")

    if total_ok == 0 and total_skipped == 0:
        print(
            "  No documents were ingested. Put PDFs in "
            f"{settings.knowledge_base_path}\\<Department>\\ and run again.\n"
        )

    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
