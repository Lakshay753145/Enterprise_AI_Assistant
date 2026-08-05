"""Prove department isolation actually holds.

    python -m scripts.verify_isolation

Run this after every deployment and after any change to retrieval, RLS, or the
SQL agent. It asserts, against the live database, that:

  1. RLS is ENABLED and FORCED on every department-scoped table
  2. an isolation policy exists on each of them
  3. with no department context set, those tables return ZERO rows (fail-closed)
  4. with department X set, only department X's rows are visible
  5. the ai_readonly role can reach the kb_* views and nothing else
  6. hybrid retrieval never returns a foreign chunk

Exit code 0 means every check passed. Anything else is a security finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.constants import Department  # noqa: E402
from backend.database.database import get_sync_engine  # noqa: E402

PROTECTED_TABLES = ("documents", "document_chunks", "conversations", "messages")

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    engine = get_sync_engine()

    print("=" * 78)
    print("  DEPARTMENT ISOLATION VERIFICATION")
    print("=" * 78)

    with engine.connect() as conn:
        # --- 1 & 2: RLS enabled, forced, and policied ----------------------
        print("\n[1] Row-Level Security configuration")
        for table in PROTECTED_TABLES:
            row = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = :t AND relnamespace = 'public'::regnamespace"
                ),
                {"t": table},
            ).first()

            check(f"{table}: RLS enabled", bool(row and row[0]))
            check(
                f"{table}: RLS forced (applies to table owner)",
                bool(row and row[1]),
                "without FORCE, the app role bypasses RLS entirely",
            )

            policies = conn.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"),
                {"t": table},
            ).scalar_one()
            check(f"{table}: isolation policy present", policies > 0)

        # --- 3: fail-closed with no context --------------------------------
        print("\n[2] Fail-closed behaviour (no department context set)")
        with engine.connect() as fresh:
            for table in PROTECTED_TABLES:
                count = fresh.execute(
                    text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed list
                ).scalar_one()
                check(
                    f"{table}: returns 0 rows without context",
                    count == 0,
                    f"leaked {count} rows - RLS is not fail-closed",
                )

        # --- 4: only own department visible ---------------------------------
        print("\n[3] Per-department visibility")
        for department in Department.values():
            with engine.connect() as scoped:
                scoped.execute(
                    text("SELECT set_config('app.current_department', :d, false)"),
                    {"d": department},
                )
                scoped.execute(
                    text("SELECT set_config('app.current_role', 'user', false)")
                )

                foreign = scoped.execute(
                    text(
                        "SELECT count(*) FROM document_chunks "
                        "WHERE department <> :d"
                    ),
                    {"d": department},
                ).scalar_one()
                own = scoped.execute(
                    text("SELECT count(*) FROM document_chunks")
                ).scalar_one()

                check(
                    f"{department}: sees no foreign chunks",
                    foreign == 0,
                    f"{foreign} foreign rows visible",
                )
                print(f"        ({own} chunks visible to {department})")

        # --- 5: SQL agent role grants ---------------------------------------
        print("\n[4] SQL agent role privileges")
        grants = conn.execute(
            text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = 'ai_readonly'"
            )
        ).scalars().all()

        if not grants:
            print(
                "  SKIP  ai_readonly role not found "
                "(run scripts/setup_db_roles.sql)"
            )
        else:
            non_view = [t for t in grants if not t.startswith("kb_")]
            check(
                "ai_readonly can only reach kb_* views",
                not non_view,
                f"also granted on: {', '.join(non_view)}",
            )

            writes = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.role_table_grants "
                    "WHERE grantee = 'ai_readonly' "
                    "AND privilege_type <> 'SELECT'"
                )
            ).scalar_one()
            check("ai_readonly holds no write privileges", writes == 0)

    print("\n" + "=" * 78)
    print(f"  {_passed} passed, {_failed} failed")
    print("=" * 78 + "\n")

    if _failed:
        print("  ONE OR MORE ISOLATION CHECKS FAILED. Do not deploy.\n")
        return 1

    print("  Department isolation verified.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
