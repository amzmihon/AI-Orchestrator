"""
Tests for the RBAC permissions module.
"""

import pytest
from rbac.permissions import (
    get_allowed_tables,
    get_denied_columns,
    check_table_access,
    get_all_known_tables,
    reload_role_config,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure role config is reloaded for each test."""
    reload_role_config()
    yield
    reload_role_config()


class TestGetAllowedTables:

    def test_admin_gets_all_tables(self):
        tables = get_allowed_tables("admin")
        all_tables = get_all_known_tables()
        assert set(tables) == set(all_tables)

    def test_hr_manager_tables(self):
        tables = get_allowed_tables("hr_manager")
        assert "employees" in tables
        assert "leave_requests" in tables
        assert "attendance" in tables
        assert "projects" in tables
        assert "project_assignments" in tables
        assert "employee_skills" in tables
        assert "sales" not in tables

    def test_sales_manager_tables(self):
        tables = get_allowed_tables("sales_manager")
        assert "sales" in tables
        assert "monthly_revenue" in tables
        assert "departments" in tables
        assert "project_assignments" in tables
        assert "employees" not in tables

    def test_employee_minimal_access(self):
        tables = get_allowed_tables("employee")
        assert "leave_requests" in tables
        assert "attendance" in tables
        assert "projects" in tables
        assert "employees" not in tables
        assert "sales" not in tables

    def test_unknown_role_gets_nothing(self):
        tables = get_allowed_tables("nonexistent_role")
        assert tables == []

    def test_role_names_are_case_insensitive(self):
        tables_lower = get_allowed_tables("hr_manager")
        tables_upper = get_allowed_tables("HR_Manager")
        assert tables_lower == tables_upper

    def test_role_name_with_spaces(self):
        """Roles with spaces like 'HR Manager' → 'hr_manager' mapping."""
        tables = get_allowed_tables("HR Manager")
        assert "employees" in tables


class TestGetDeniedColumns:

    def test_hr_manager_denied_salary(self):
        denied = get_denied_columns("hr_manager")
        assert "salary" in denied.get("employees", [])
        assert "bank_account" in denied.get("employees", [])

    def test_admin_no_denied_columns(self):
        denied = get_denied_columns("admin")
        assert denied == {}

    def test_sales_staff_commission_hidden(self):
        denied = get_denied_columns("sales_staff")
        assert "commission_rate" in denied.get("sales", [])


class TestCheckTableAccess:

    def test_all_allowed(self):
        violations = check_table_access("admin", ["employees", "sales"])
        assert violations == []

    def test_some_forbidden(self):
        violations = check_table_access("employee", ["employees", "sales"])
        assert "employees" in violations
        assert "sales" in violations

    def test_partial_access(self):
        violations = check_table_access("hr_manager", ["employees", "sales"])
        assert "employees" not in violations
        assert "sales" in violations
