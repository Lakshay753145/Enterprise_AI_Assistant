"""Tests for the department isolation guards.

These need no database or LLM - they exercise the pure-Python layer that is the
last line of defence. Run with:

    pytest backend/tests -v
"""

from dataclasses import dataclass

import pytest

from backend.core.constants import Department, Role
from backend.core.exceptions import DepartmentIsolationError
from backend.security.isolation import (
    assert_department_match,
    assert_dicts_in_scope,
    assert_record_in_scope,
    filter_records_in_scope,
)


@dataclass
class FakeRecord:
    id: int
    department: str


class TestAssertRecordInScope:
    def test_allows_own_department(self):
        record = FakeRecord(1, Department.HR.value)
        assert_record_in_scope(record, department=Department.HR.value)

    def test_blocks_other_department(self):
        record = FakeRecord(1, Department.FINANCE.value)
        with pytest.raises(DepartmentIsolationError):
            assert_record_in_scope(record, department=Department.HR.value)

    def test_none_is_not_a_violation(self):
        assert_record_in_scope(None, department=Department.HR.value)

    def test_super_admin_crosses_boundaries(self):
        record = FakeRecord(1, Department.FINANCE.value)
        assert_record_in_scope(
            record,
            department=Department.HR.value,
            role=Role.SUPER_ADMIN.value,
        )

    def test_department_admin_does_not_cross_boundaries(self):
        """An admin administers their own department, not everyone's."""
        record = FakeRecord(1, Department.FINANCE.value)
        with pytest.raises(DepartmentIsolationError):
            assert_record_in_scope(
                record,
                department=Department.HR.value,
                role=Role.ADMIN.value,
            )


class TestFilterRecordsInScope:
    def test_passes_through_clean_set(self):
        records = [FakeRecord(i, Department.IT.value) for i in range(5)]
        result = filter_records_in_scope(records, department=Department.IT.value)
        assert len(result) == 5

    def test_raises_rather_than_silently_dropping(self):
        """A silent drop would hide the bug that caused the leak."""
        records = [
            FakeRecord(1, Department.IT.value),
            FakeRecord(2, Department.PRODUCTION.value),
        ]
        with pytest.raises(DepartmentIsolationError):
            filter_records_in_scope(records, department=Department.IT.value)

    def test_empty_is_fine(self):
        assert filter_records_in_scope([], department=Department.IT.value) == []


class TestAssertDictsInScope:
    def test_allows_matching_rows(self):
        rows = [{"id": 1, "department": Department.PURCHASE.value}]
        assert_dicts_in_scope(rows, department=Department.PURCHASE.value)

    def test_blocks_foreign_row(self):
        rows = [
            {"id": 1, "department": Department.PURCHASE.value},
            {"id": 2, "department": Department.HR.value},
        ]
        with pytest.raises(DepartmentIsolationError):
            assert_dicts_in_scope(rows, department=Department.PURCHASE.value)

    def test_missing_department_key_is_a_violation(self):
        """Fail closed: a row we cannot attribute must not be returned."""
        with pytest.raises(DepartmentIsolationError):
            assert_dicts_in_scope(
                [{"id": 1}], department=Department.PURCHASE.value
            )


class TestAssertDepartmentMatch:
    def test_returns_own_department_when_unspecified(self):
        assert (
            assert_department_match(None, department=Department.HR.value)
            == Department.HR.value
        )

    def test_allows_explicitly_asking_for_own_department(self):
        assert (
            assert_department_match(
                Department.HR.value, department=Department.HR.value
            )
            == Department.HR.value
        )

    def test_blocks_asking_for_another_department(self):
        with pytest.raises(DepartmentIsolationError):
            assert_department_match(
                Department.FINANCE.value, department=Department.HR.value
            )

    def test_super_admin_may_request_any_department(self):
        assert (
            assert_department_match(
                Department.FINANCE.value,
                department=Department.HR.value,
                role=Role.SUPER_ADMIN.value,
            )
            == Department.FINANCE.value
        )


class TestDepartmentConstants:
    def test_every_department_has_a_scope_description(self):
        """The relevance gate prompt depends on this mapping being complete."""
        from backend.core.constants import DEPARTMENT_SCOPE

        for department in Department.values():
            assert department in DEPARTMENT_SCOPE
            assert len(DEPARTMENT_SCOPE[department]) > 40

    def test_validation_rejects_unknown_department(self):
        assert not Department.is_valid("Marketing")
        assert not Department.is_valid("")
        assert Department.is_valid("Production")
