"""Department isolation guards - the last line of defence.

Layers protecting department data, outermost first:

  1. The JWT carries the department; the client never supplies it.
  2. Every repository query filters on that department in SQL.
  3. Postgres RLS re-applies the same predicate inside the database.
  4. **This module** re-checks every record after it comes back.
  5. The SQL agent only ever sees department-scoped read-only views.

Layer 4 exists because layers 2 and 3 are code that a future change could get
wrong. If a single foreign record ever reaches this point, that is a security
incident: it is logged at CRITICAL, the request is failed, and nothing is
returned to the user.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, Sequence, TypeVar

from backend.core.constants import Role
from backend.core.exceptions import DepartmentIsolationError
from backend.core.logging_config import app_logger, write_audit_event


class HasDepartment(Protocol):
    department: str


T = TypeVar("T", bound=HasDepartment)


def _is_cross_department_role(role: str) -> bool:
    return role == Role.SUPER_ADMIN.value


def assert_record_in_scope(
    record: HasDepartment | None,
    *,
    department: str,
    role: str = Role.USER.value,
    username: str | None = None,
    resource: str = "record",
    resource_id: Any = None,
) -> None:
    """Fail loudly if a single record belongs to another department."""
    if record is None:
        return
    if _is_cross_department_role(role):
        return

    actual = getattr(record, "department", None)
    if actual == department:
        return

    _report_violation(
        username=username,
        expected=department,
        actual=actual,
        role=role,
        resource=resource,
        resource_id=resource_id,
    )
    raise DepartmentIsolationError()


def filter_records_in_scope(
    records: Sequence[T],
    *,
    department: str,
    role: str = Role.USER.value,
    username: str | None = None,
    resource: str = "records",
) -> list[T]:
    """Return only in-scope records, raising if any were out of scope.

    Deliberately raises rather than silently dropping. A silent drop would hide
    the bug that produced the leak; a raised error surfaces it immediately in
    the error log and in the audit trail.
    """
    if _is_cross_department_role(role):
        return list(records)

    in_scope: list[T] = []
    violations: list[Any] = []

    for record in records:
        if getattr(record, "department", None) == department:
            in_scope.append(record)
        else:
            violations.append(getattr(record, "id", "?"))

    if violations:
        _report_violation(
            username=username,
            expected=department,
            actual="mixed",
            role=role,
            resource=resource,
            resource_id=violations[:20],
        )
        raise DepartmentIsolationError()

    return in_scope


def assert_dicts_in_scope(
    rows: Iterable[dict[str, Any]],
    *,
    department: str,
    role: str = Role.USER.value,
    username: str | None = None,
    key: str = "department",
    resource: str = "rows",
) -> None:
    """Same check for plain dict rows (raw SQL results from retrieval)."""
    if _is_cross_department_role(role):
        return

    bad = [r for r in rows if r.get(key) != department]
    if bad:
        _report_violation(
            username=username,
            expected=department,
            actual={r.get(key) for r in bad},
            role=role,
            resource=resource,
            resource_id=[r.get("id") for r in bad[:20]],
        )
        raise DepartmentIsolationError()


def assert_department_match(
    requested: str | None,
    *,
    department: str,
    role: str = Role.USER.value,
    username: str | None = None,
) -> str:
    """Validate a client-supplied department against the token's department.

    Any endpoint that accepts a department parameter must run it through here.
    Non-super-admins get their own department back regardless of what they
    asked for, and asking for someone else's is recorded as an attempt.
    """
    if _is_cross_department_role(role):
        return requested or department

    if requested and requested != department:
        _report_violation(
            username=username,
            expected=department,
            actual=requested,
            role=role,
            resource="department_parameter",
            resource_id=None,
        )
        raise DepartmentIsolationError(
            "You may only access data for your own department."
        )

    return department


def _report_violation(
    *,
    username: str | None,
    expected: str,
    actual: Any,
    role: str,
    resource: str,
    resource_id: Any,
) -> None:
    app_logger.critical(
        "DEPARTMENT ISOLATION VIOLATION | user=%s role=%s resource=%s "
        "expected=%s actual=%s ids=%s"
        % (username or "?", role, resource, expected, actual, resource_id)
    )
    write_audit_event(
        "department_isolation_violation",
        username=username,
        department=expected,
        role=role,
        success=False,
        detail={
            "resource": resource,
            "expected_department": expected,
            "actual_department": str(actual),
            "resource_ids": resource_id,
        },
    )
