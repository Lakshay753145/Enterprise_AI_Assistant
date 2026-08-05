"""Create the initial user accounts.

    python -m scripts.seed_users                 # create demo accounts
    python -m scripts.seed_users --show-passwords
    python -m scripts.seed_users --reset         # reset passwords of existing users

Creates one admin and one standard user per department, plus a super admin.

Passwords are generated randomly and printed ONCE. They are not stored
anywhere in plaintext. Change them after first sign-in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.core.constants import Department, Role  # noqa: E402
from backend.database.database import get_sync_session_factory  # noqa: E402
from backend.models.users import User  # noqa: E402
from backend.security.hash import generate_password, hash_password  # noqa: E402


def build_seed_users() -> list[dict[str, str]]:
    users: list[dict[str, str]] = [
        {
            "username": "superadmin",
            "email": "it.admin@aerolloy.com",
            "full_name": "IT Super Administrator",
            "department": Department.IT.value,
            "role": Role.SUPER_ADMIN.value,
        }
    ]

    for department in Department.values():
        slug = department.lower()
        users.append(
            {
                "username": f"{slug}.admin",
                "email": f"{slug}.admin@aerolloy.com",
                "full_name": f"{department} Administrator",
                "department": department,
                "role": Role.ADMIN.value,
            }
        )
        users.append(
            {
                "username": f"{slug}.user",
                "email": f"{slug}.user@aerolloy.com",
                "full_name": f"{department} User",
                "department": department,
                "role": Role.USER.value,
            }
        )

    return users


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed initial user accounts.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset the password of accounts that already exist.",
    )
    parser.add_argument(
        "--show-passwords",
        action="store_true",
        default=True,
        help="Print generated passwords (default: on - this is your only chance).",
    )
    args = parser.parse_args()

    session = get_sync_session_factory()()
    created: list[tuple[str, str, str, str]] = []
    skipped: list[str] = []

    try:
        for spec in build_seed_users():
            existing = session.execute(
                select(User).where(User.username == spec["username"])
            ).scalar_one_or_none()

            password = generate_password(14)

            if existing is not None:
                if not args.reset:
                    skipped.append(spec["username"])
                    continue
                existing.password_hash = hash_password(password)
                existing.failed_login_attempts = 0
                existing.locked_until = None
                existing.is_active = True
                created.append(
                    (spec["username"], password, spec["department"], spec["role"])
                )
                continue

            session.add(
                User(
                    username=spec["username"],
                    email=spec["email"],
                    full_name=spec["full_name"],
                    password_hash=hash_password(password),
                    department=spec["department"],
                    role=spec["role"],
                    is_active=True,
                )
            )
            created.append(
                (spec["username"], password, spec["department"], spec["role"])
            )

        session.commit()
    except Exception as exc:
        session.rollback()
        print(f"\nFailed: {exc}", file=sys.stderr)
        print(
            "\nHave you run the migration?  alembic upgrade head",
            file=sys.stderr,
        )
        return 1
    finally:
        session.close()

    if skipped:
        print(f"\nSkipped {len(skipped)} existing account(s): {', '.join(skipped)}")
        print("Use --reset to regenerate their passwords.")

    if not created:
        print("\nNothing to do.")
        return 0

    width = 78
    print("\n" + "=" * width)
    print("  ACCOUNTS CREATED - SAVE THESE PASSWORDS NOW, THEY ARE NOT RECOVERABLE")
    print("=" * width)
    print(f"  {'USERNAME':<20} {'PASSWORD':<18} {'DEPARTMENT':<14} ROLE")
    print("  " + "-" * (width - 4))
    for username, password, department, role in created:
        shown = password if args.show_passwords else "*" * 14
        print(f"  {username:<20} {shown:<18} {department:<14} {role}")
    print("=" * width)
    print("\n  Sign in with USERNAME + PASSWORD + DEPARTMENT. All three must match.")
    print("  Change these passwords after first sign-in.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
