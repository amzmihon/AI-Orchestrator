"""
Security Stress Tests — validates that the system blocks all forms of
data leakage, SQL injection, RBAC bypass, and sensitive data access.

This test suite acts as a penetration test against:
  1. SQL Guardrail (injection, dangerous operations)
  2. RBAC layer (role-based table/column access)
  3. Sensitive data protection (passwords, SSNs, bank accounts, etc.)
  4. Hidden table protection (user_credentials, audit_log)
"""

import pytest
from query_engine.sql_guardrail import (
    validate,
    extract_table_names,
    extract_cte_aliases,
    SQLGuardrailError,
)
from rbac.permissions import (
    get_allowed_tables,
    get_denied_columns,
    check_table_access,
    get_all_known_tables,
    reload_role_config,
)


# ── Fixtures ─────────────────────────────────────────────

ALL_TABLES = [
    "employees", "sales", "attendance", "projects", "project_assignments",
    "leave_requests", "monthly_revenue", "departments", "customers",
    "employee_skills", "expenses", "payroll", "performance_reviews",
]

# Tables that should NEVER be accessible through the AI system
HIDDEN_TABLES = ["user_credentials", "audit_log"]

# Sensitive columns that should be blocked for most roles
SENSITIVE_EMPLOYEE_COLUMNS = ["ssn", "bank_account", "national_id", "emergency_contact"]

# These should NEVER appear in schema or be queryable
CREDENTIAL_COLUMNS = ["password_hash", "mfa_secret", "api_token"]


@pytest.fixture(autouse=True)
def _reload_config():
    """Ensure role config is fresh for each test."""
    reload_role_config()
    yield
    reload_role_config()


# ═══════════════════════════════════════════════════════════
# 1. PASSWORD HASH / CREDENTIAL LEAKAGE PREVENTION
# ═══════════════════════════════════════════════════════════

class TestPasswordHashLeakage:
    """Verify that password hashes, MFA secrets, and API tokens
    can NEVER be extracted through any SQL query."""

    def test_select_from_user_credentials_blocked(self):
        """Direct SELECT from user_credentials must fail."""
        sql = "SELECT * FROM user_credentials"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_select_password_hash_from_user_credentials(self):
        sql = "SELECT username, password_hash FROM user_credentials"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_select_mfa_secret_blocked(self):
        sql = "SELECT employee_id, mfa_secret FROM user_credentials"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_select_api_token_blocked(self):
        sql = "SELECT username, api_token FROM user_credentials WHERE api_token IS NOT NULL"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_join_employees_to_credentials(self):
        """JOIN from employees to credentials should be blocked."""
        sql = (
            "SELECT e.first_name, uc.password_hash "
            "FROM employees e "
            "JOIN user_credentials uc ON e.id = uc.employee_id"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_credentials_not_in_allowed_tables_any_role(self):
        """user_credentials should not appear in any role's allowed tables."""
        roles = [
            "admin", "hr_manager", "hr_staff", "sales_manager",
            "sales_staff", "project_manager", "finance", "employee"
        ]
        for role in roles:
            tables = get_allowed_tables(role)
            assert "user_credentials" not in tables, \
                f"Role '{role}' should NOT have access to user_credentials"

    def test_credentials_not_in_all_known_tables(self):
        """user_credentials should not be in the known tables list."""
        all_tables = get_all_known_tables()
        assert "user_credentials" not in all_tables

    def test_audit_log_not_in_all_known_tables(self):
        """audit_log should not be in the known tables list."""
        all_tables = get_all_known_tables()
        assert "audit_log" not in all_tables

    def test_subquery_to_credentials(self):
        """Attempt to read credentials via subquery."""
        sql = (
            "SELECT * FROM employees WHERE id IN "
            "(SELECT employee_id FROM user_credentials WHERE is_locked = TRUE)"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_cte_wrapping_credentials(self):
        """CTE wrapping user_credentials should still be blocked."""
        sql = (
            "WITH creds AS (SELECT * FROM user_credentials) "
            "SELECT username, password_hash FROM creds"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_select_all_locked_accounts(self):
        """Querying locked accounts reveals auth state."""
        sql = "SELECT employee_id, failed_attempts FROM user_credentials WHERE is_locked = TRUE"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)


# ═══════════════════════════════════════════════════════════
# 2. AUDIT LOG ACCESS PREVENTION
# ═══════════════════════════════════════════════════════════

class TestAuditLogProtection:
    """Ensure audit_log is invisible to the AI layer."""

    def test_select_from_audit_log_blocked(self):
        sql = "SELECT * FROM audit_log"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_audit_log_view_salary_events(self):
        """Trying to see who viewed salary data."""
        sql = "SELECT performed_by, created_at FROM audit_log WHERE action = 'VIEW_SALARY'"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_audit_log_failed_logins(self):
        """Trying to see failed login attempts."""
        sql = "SELECT performed_by, ip_address FROM audit_log WHERE action = 'FAILED_LOGIN'"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_join_audit_log_to_employees(self):
        sql = (
            "SELECT e.first_name, al.action, al.ip_address "
            "FROM employees e "
            "JOIN audit_log al ON e.id = al.performed_by"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_audit_log_not_in_any_role(self):
        roles = [
            "admin", "hr_manager", "hr_staff", "sales_manager",
            "sales_staff", "project_manager", "finance", "employee"
        ]
        for role in roles:
            tables = get_allowed_tables(role)
            assert "audit_log" not in tables, \
                f"Role '{role}' should NOT have access to audit_log"


# ═══════════════════════════════════════════════════════════
# 3. SENSITIVE EMPLOYEE COLUMN PROTECTION
# ═══════════════════════════════════════════════════════════

class TestSensitiveColumnProtection:
    """Verify that SSN, bank_account, national_id, and emergency_contact
    are properly restricted via RBAC denied_columns."""

    def test_hr_staff_cannot_see_ssn(self):
        denied = get_denied_columns("hr_staff")
        assert "ssn" in denied.get("employees", [])

    def test_hr_staff_cannot_see_bank_account(self):
        denied = get_denied_columns("hr_staff")
        assert "bank_account" in denied.get("employees", [])

    def test_hr_staff_cannot_see_national_id(self):
        denied = get_denied_columns("hr_staff")
        assert "national_id" in denied.get("employees", [])

    def test_hr_staff_cannot_see_emergency_contact(self):
        denied = get_denied_columns("hr_staff")
        assert "emergency_contact" in denied.get("employees", [])

    def test_hr_manager_cannot_see_ssn(self):
        denied = get_denied_columns("hr_manager")
        assert "ssn" in denied.get("employees", [])

    def test_hr_manager_cannot_see_national_id(self):
        denied = get_denied_columns("hr_manager")
        assert "national_id" in denied.get("employees", [])

    def test_project_manager_denied_all_sensitive(self):
        denied = get_denied_columns("project_manager")
        emp_denied = denied.get("employees", [])
        for col in ["salary", "bank_account", "ssn", "national_id", "emergency_contact"]:
            assert col in emp_denied, \
                f"project_manager should not see employees.{col}"

    def test_finance_cannot_see_employee_ssn(self):
        denied = get_denied_columns("finance")
        assert "ssn" in denied.get("employees", [])

    def test_finance_cannot_see_employee_national_id(self):
        denied = get_denied_columns("finance")
        assert "national_id" in denied.get("employees", [])

    def test_admin_has_no_denied_columns(self):
        denied = get_denied_columns("admin")
        assert denied == {}

    def test_employee_role_has_no_employee_table_access(self):
        """The 'employee' role shouldn't even have employees table access."""
        tables = get_allowed_tables("employee")
        assert "employees" not in tables

    def test_sales_staff_cannot_access_employees(self):
        """Sales staff should not have employees table at all."""
        tables = get_allowed_tables("sales_staff")
        assert "employees" not in tables


# ═══════════════════════════════════════════════════════════
# 4. PAYROLL TABLE RBAC ENFORCEMENT
# ═══════════════════════════════════════════════════════════

class TestPayrollSecurity:
    """Payroll data is highly sensitive — only finance and admin should see it."""

    def test_finance_can_access_payroll(self):
        tables = get_allowed_tables("finance")
        assert "payroll" in tables

    def test_admin_can_access_payroll(self):
        tables = get_allowed_tables("admin")
        assert "payroll" in tables

    def test_hr_manager_cannot_access_payroll(self):
        tables = get_allowed_tables("hr_manager")
        assert "payroll" not in tables

    def test_hr_staff_cannot_access_payroll(self):
        tables = get_allowed_tables("hr_staff")
        assert "payroll" not in tables

    def test_sales_manager_cannot_access_payroll(self):
        tables = get_allowed_tables("sales_manager")
        assert "payroll" not in tables

    def test_employee_cannot_access_payroll(self):
        tables = get_allowed_tables("employee")
        assert "payroll" not in tables

    def test_project_manager_cannot_access_payroll(self):
        tables = get_allowed_tables("project_manager")
        assert "payroll" not in tables

    def test_guardrail_blocks_payroll_for_non_finance(self):
        """Guardrail should block payroll queries with sales-only tables."""
        sql = "SELECT employee_id, net_pay, bank_reference FROM payroll"
        sales_tables = get_allowed_tables("sales_staff")
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, sales_tables)


# ═══════════════════════════════════════════════════════════
# 5. PERFORMANCE REVIEWS TABLE RBAC
# ═══════════════════════════════════════════════════════════

class TestPerformanceReviewSecurity:
    """Performance reviews contain salary recommendations and ratings —
    should only be accessible to HR managers and admin."""

    def test_hr_manager_can_access_performance_reviews(self):
        tables = get_allowed_tables("hr_manager")
        assert "performance_reviews" in tables

    def test_admin_can_access_performance_reviews(self):
        tables = get_allowed_tables("admin")
        assert "performance_reviews" in tables

    def test_hr_staff_cannot_access_performance_reviews(self):
        tables = get_allowed_tables("hr_staff")
        assert "performance_reviews" not in tables

    def test_sales_staff_cannot_access_reviews(self):
        tables = get_allowed_tables("sales_staff")
        assert "performance_reviews" not in tables

    def test_employee_cannot_access_reviews(self):
        tables = get_allowed_tables("employee")
        assert "performance_reviews" not in tables

    def test_finance_cannot_access_reviews(self):
        tables = get_allowed_tables("finance")
        assert "performance_reviews" not in tables


# ═══════════════════════════════════════════════════════════
# 6. SQL INJECTION — CLASSIC ATTACKS
# ═══════════════════════════════════════════════════════════

class TestSQLInjectionClassic:
    """Classic SQL injection patterns from OWASP Top 10."""

    def test_union_select_passwords(self):
        sql = "SELECT first_name FROM employees UNION SELECT password_hash FROM user_credentials"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_stacked_query_drop_table(self):
        sql = "SELECT id FROM employees; DROP TABLE employees"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_stacked_query_delete(self):
        sql = "SELECT 1 FROM employees; DELETE FROM employees"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_stacked_query_update_salary(self):
        sql = "SELECT id FROM employees; UPDATE employees SET salary = 999999 WHERE id = 1"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_stacked_query_insert_admin(self):
        sql = "SELECT 1; INSERT INTO user_credentials (username, password_hash) VALUES ('hacker', 'x')"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_comment_injection_double_dash(self):
        sql = "SELECT * FROM employees WHERE id = 1 -- AND department = 'Sales'"
        with pytest.raises(SQLGuardrailError, match="SQL comment"):
            validate(sql, ALL_TABLES)

    def test_comment_injection_block_comment(self):
        sql = "SELECT * FROM employees WHERE 1=1 /* bypass */"
        with pytest.raises(SQLGuardrailError, match="Block comment"):
            validate(sql, ALL_TABLES)

    def test_tautology_attack(self):
        """1=1 tautology — guardrail allows this (it's just a WHERE clause)
        but actual data access is controlled by RBAC + table permissions."""
        sql = "SELECT * FROM employees WHERE 1=1"
        result = validate(sql, ALL_TABLES)
        assert result  # Valid SQL, but RBAC would limit columns

    def test_or_1_equals_1_injection(self):
        sql = "SELECT * FROM employees WHERE id = 1 OR 1=1"
        result = validate(sql, ALL_TABLES)
        assert result  # Guardrail allows; RBAC limits actual data


# ═══════════════════════════════════════════════════════════
# 7. SQL INJECTION — ADVANCED / EVASION
# ═══════════════════════════════════════════════════════════

class TestSQLInjectionAdvanced:
    """Advanced injection evasion techniques."""

    def test_case_variation_drop(self):
        """Mixed case to bypass naive filters."""
        sql = "SELECT id FROM employees; dRoP TABLE employees"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_case_variation_delete(self):
        sql = "SELECT 1; DeLeTe FROM sales"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_case_variation_update(self):
        sql = "SELECT 1; UpDaTe employees SET salary = 0"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_exec_command(self):
        sql = "EXEC xp_cmdshell 'whoami'"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_execute_procedure(self):
        sql = "EXECUTE sp_configure 'xp_cmdshell', 1"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_pg_sleep_dos(self):
        sql = "SELECT pg_sleep(30)"
        with pytest.raises(SQLGuardrailError, match="pg_sleep"):
            validate(sql, ALL_TABLES)

    def test_into_outfile(self):
        sql = "SELECT * FROM employees INTO OUTFILE '/tmp/hack.csv'"
        with pytest.raises(SQLGuardrailError, match="File write"):
            validate(sql, ALL_TABLES)

    def test_load_file(self):
        sql = "SELECT LOAD_FILE('/etc/passwd')"
        with pytest.raises(SQLGuardrailError, match="File read"):
            validate(sql, ALL_TABLES)

    def test_into_dumpfile(self):
        sql = "SELECT password_hash FROM user_credentials INTO DUMPFILE '/tmp/dump'"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_information_schema_enumeration(self):
        sql = "SELECT table_name FROM INFORMATION_SCHEMA.TABLES"
        with pytest.raises(SQLGuardrailError, match="Schema enumeration"):
            validate(sql, ALL_TABLES)

    def test_information_schema_columns(self):
        sql = "SELECT column_name FROM INFORMATION_SCHEMA.COLUMNS WHERE table_name = 'user_credentials'"
        with pytest.raises(SQLGuardrailError, match="Schema enumeration"):
            validate(sql, ALL_TABLES)

    def test_pg_catalog_access(self):
        sql = "SELECT tablename FROM pg_catalog.pg_tables"
        with pytest.raises(SQLGuardrailError, match="System catalog"):
            validate(sql, ALL_TABLES)

    def test_pg_catalog_roles(self):
        sql = "SELECT rolname, rolpassword FROM pg_catalog.pg_authid"
        with pytest.raises(SQLGuardrailError, match="System catalog"):
            validate(sql, ALL_TABLES)

    def test_grant_privilege_escalation(self):
        sql = "GRANT ALL PRIVILEGES ON ALL TABLES TO public"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_create_table_injection(self):
        sql = "CREATE TABLE hack_dump AS SELECT * FROM user_credentials"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_alter_table_injection(self):
        sql = "ALTER TABLE employees ADD COLUMN hacked BOOLEAN DEFAULT TRUE"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_truncate_injection(self):
        sql = "TRUNCATE TABLE employees"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_merge_injection(self):
        sql = "MERGE INTO employees USING (SELECT 1) ON (1=1) WHEN MATCHED THEN UPDATE SET salary = 0"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_call_procedure(self):
        sql = "CALL sp_execute_external_script @language = N'Python', @script = N'import os; os.system(\"whoami\")'"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_xp_cmdshell_in_select(self):
        sql = "SELECT * FROM employees WHERE first_name = ''; EXEC xp_cmdshell('whoami')"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)


# ═══════════════════════════════════════════════════════════
# 8. RBAC BYPASS ATTEMPTS — CROSS-ROLE DATA ACCESS
# ═══════════════════════════════════════════════════════════

class TestRBACBypass:
    """Test that each role cannot access data outside its permissions."""

    def test_sales_staff_cannot_read_employees(self):
        tables = get_allowed_tables("sales_staff")
        sql = "SELECT first_name, salary FROM employees"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_sales_staff_cannot_read_payroll(self):
        tables = get_allowed_tables("sales_staff")
        sql = "SELECT * FROM payroll"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_employee_cannot_read_sales(self):
        tables = get_allowed_tables("employee")
        sql = "SELECT amount FROM sales"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_employee_cannot_read_expenses(self):
        tables = get_allowed_tables("employee")
        sql = "SELECT * FROM expenses"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_employee_cannot_read_performance_reviews(self):
        tables = get_allowed_tables("employee")
        sql = "SELECT rating, salary_recommendation FROM performance_reviews"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_hr_staff_cannot_read_payroll(self):
        tables = get_allowed_tables("hr_staff")
        sql = "SELECT employee_id, net_pay FROM payroll"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_hr_staff_cannot_read_sales(self):
        tables = get_allowed_tables("hr_staff")
        sql = "SELECT amount, date FROM sales"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_sales_manager_cannot_read_leave_requests(self):
        tables = get_allowed_tables("sales_manager")
        sql = "SELECT * FROM leave_requests"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_project_manager_cannot_read_payroll(self):
        tables = get_allowed_tables("project_manager")
        sql = "SELECT * FROM payroll"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_project_manager_cannot_read_revenue(self):
        tables = get_allowed_tables("project_manager")
        sql = "SELECT * FROM monthly_revenue"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_finance_cannot_read_performance_reviews(self):
        tables = get_allowed_tables("finance")
        sql = "SELECT * FROM performance_reviews"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)

    def test_finance_cannot_read_attendance(self):
        tables = get_allowed_tables("finance")
        sql = "SELECT * FROM attendance"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, tables)


# ═══════════════════════════════════════════════════════════
# 9. COMPLEX ATTACK SCENARIOS — MULTI-STEP / CHAINED
# ═══════════════════════════════════════════════════════════

class TestComplexAttackScenarios:
    """Simulate real-world multi-step attack chains."""

    def test_credential_exfil_via_union(self):
        """Attempt to UNION SELECT credentials into a normal query."""
        sql = (
            "SELECT first_name, email FROM employees "
            "UNION SELECT username, password_hash FROM user_credentials"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_credential_exfil_via_union_all(self):
        sql = (
            "SELECT id, email FROM employees "
            "UNION ALL SELECT id, password_hash FROM user_credentials"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_payroll_exfil_via_join_for_sales_role(self):
        """Sales role attempting to join payroll data."""
        sales_tables = get_allowed_tables("sales_staff")
        sql = (
            "SELECT s.amount, p.net_pay "
            "FROM sales s "
            "JOIN payroll p ON s.rep_id = p.employee_id"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, sales_tables)

    def test_nested_subquery_credential_access(self):
        sql = (
            "SELECT first_name FROM employees "
            "WHERE id = (SELECT employee_id FROM user_credentials "
            "            WHERE password_hash LIKE '$2b$12$%' LIMIT 1)"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_schema_enum_via_information_schema_to_find_creds(self):
        sql = (
            "SELECT column_name FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE table_name = 'user_credentials'"
        )
        with pytest.raises(SQLGuardrailError, match="Schema enumeration"):
            validate(sql, ALL_TABLES)

    def test_cross_role_join_employees_payroll(self):
        """HR trying to see payroll via employee join."""
        hr_tables = get_allowed_tables("hr_staff")
        sql = (
            "SELECT e.first_name, p.net_pay "
            "FROM employees e "
            "JOIN payroll p ON e.id = p.employee_id"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, hr_tables)

    def test_multiline_injection_attempt(self):
        sql = """SELECT id
FROM employees
WHERE id = 1;
DROP TABLE user_credentials"""
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_tab_separated_stacked_query(self):
        sql = "SELECT id FROM employees;\tDROP TABLE employees"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)

    def test_newline_separated_stacked_query(self):
        sql = "SELECT id FROM employees;\nDELETE FROM sales"
        with pytest.raises(SQLGuardrailError):
            validate(sql, ALL_TABLES)


# ═══════════════════════════════════════════════════════════
# 10. EDGE CASES & BOUNDARY TESTS
# ═══════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_sql(self):
        with pytest.raises(SQLGuardrailError, match="empty"):
            validate("", ALL_TABLES)

    def test_whitespace_only_sql(self):
        with pytest.raises(SQLGuardrailError, match="empty"):
            validate("   \n\t  ", ALL_TABLES)

    def test_valid_query_unknown_table(self):
        sql = "SELECT * FROM secret_admin_data"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_markdown_fences_around_injection(self):
        sql = "```sql\nSELECT * FROM user_credentials\n```"
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_extremely_long_query(self):
        """Very long query should still be parsed and validated."""
        conditions = " AND ".join(
            [f"id != {i}" for i in range(500)]
        )
        sql = f"SELECT id FROM employees WHERE {conditions}"
        result = validate(sql, ALL_TABLES)
        assert "SELECT" in result

    def test_multiple_joins_all_allowed(self):
        """Complex multi-join query with all allowed tables."""
        sql = (
            "SELECT e.first_name, p.name, pa.role_in_project, sk.skill_name "
            "FROM employees e "
            "JOIN project_assignments pa ON e.id = pa.employee_id "
            "JOIN projects p ON pa.project_id = p.id "
            "JOIN employee_skills sk ON e.id = sk.employee_id"
        )
        result = validate(sql, ALL_TABLES)
        assert result

    def test_multiple_joins_one_forbidden(self):
        """Multi-join where one table is forbidden."""
        sql = (
            "SELECT e.first_name, uc.password_hash "
            "FROM employees e "
            "JOIN project_assignments pa ON e.id = pa.employee_id "
            "JOIN user_credentials uc ON e.id = uc.employee_id"
        )
        with pytest.raises(SQLGuardrailError, match="Access denied"):
            validate(sql, ALL_TABLES)

    def test_cte_alias_does_not_mask_real_table(self):
        """Using a CTE alias named like a hidden table should not bypass."""
        sql = (
            "WITH user_credentials AS "
            "(SELECT id, first_name FROM employees) "
            "SELECT * FROM user_credentials"
        )
        # The CTE alias masks user_credentials name, but the FROM inside
        # references employees which is allowed. This is actually valid
        # because it only reads from employees.
        result = validate(sql, ALL_TABLES)
        assert result

    def test_no_from_clause(self):
        """Simple expression like SELECT 1 should succeed."""
        sql = "SELECT 1 AS test_value"
        result = validate(sql, ALL_TABLES)
        assert "1" in result

    def test_case_insensitive_table_extraction(self):
        """Table names in any case should be matched."""
        sql = "SELECT * FROM EMPLOYEES"
        result = validate(sql, ALL_TABLES)
        assert result


# ═══════════════════════════════════════════════════════════
# 11. TABLE EXISTENCE VERIFICATION — HIDDEN TABLES
# ═══════════════════════════════════════════════════════════

class TestHiddenTableVerification:
    """Verify that hidden tables (user_credentials, audit_log) are
    completely invisible to the AI system at every layer."""

    def test_user_credentials_not_in_schema_map(self):
        """user_credentials should not be listed in all_tables."""
        all_tables = get_all_known_tables()
        assert "user_credentials" not in all_tables

    def test_audit_log_not_in_schema_map(self):
        all_tables = get_all_known_tables()
        assert "audit_log" not in all_tables

    def test_hidden_tables_blocked_even_for_admin(self):
        """Even admin role queries should fail for hidden tables."""
        admin_tables = get_allowed_tables("admin")
        for hidden in HIDDEN_TABLES:
            assert hidden not in admin_tables, \
                f"Admin should NOT have access to {hidden}"

    def test_guardrail_blocks_hidden_table_for_admin(self):
        """Even with admin's full table list, hidden tables fail."""
        admin_tables = get_allowed_tables("admin")
        for hidden in HIDDEN_TABLES:
            sql = f"SELECT * FROM {hidden}"
            with pytest.raises(SQLGuardrailError, match="Access denied"):
                validate(sql, admin_tables)


# ═══════════════════════════════════════════════════════════
# 12. VALID QUERIES — SMOKE TESTS (should PASS)
# ═══════════════════════════════════════════════════════════

class TestValidSecuritySmokeTests:
    """Ensure that legitimate queries still work after tightened security."""

    def test_admin_can_query_all_known_tables(self):
        admin_tables = get_allowed_tables("admin")
        for table in get_all_known_tables():
            sql = f"SELECT * FROM {table}"
            result = validate(sql, admin_tables)
            assert result

    def test_finance_can_query_payroll(self):
        finance_tables = get_allowed_tables("finance")
        sql = "SELECT employee_id, gross_pay, net_pay FROM payroll WHERE pay_period = '2026-01-01'"
        result = validate(sql, finance_tables)
        assert "payroll" in result

    def test_hr_manager_can_query_performance_reviews(self):
        hr_tables = get_allowed_tables("hr_manager")
        sql = "SELECT employee_id, rating, promotion_recommended FROM performance_reviews"
        result = validate(sql, hr_tables)
        assert "performance_reviews" in result

    def test_sales_can_query_customers(self):
        tables = get_allowed_tables("sales_staff")
        sql = "SELECT name, tier, credit_limit FROM customers WHERE is_active = TRUE"
        result = validate(sql, tables)
        assert result

    def test_employee_can_query_own_leave(self):
        tables = get_allowed_tables("employee")
        sql = "SELECT leave_type, start_date, end_date, status FROM leave_requests WHERE employee_id = 10"
        result = validate(sql, tables)
        assert result

    def test_complex_cte_with_aggregation(self):
        sql = (
            "WITH dept_count AS ("
            "  SELECT department, COUNT(*) AS emp_count FROM employees GROUP BY department"
            "), dept_budget AS ("
            "  SELECT name, budget FROM departments"
            ") "
            "SELECT dc.department, dc.emp_count, db.budget "
            "FROM dept_count dc "
            "JOIN dept_budget db ON dc.department = db.name"
        )
        result = validate(sql, ALL_TABLES)
        assert "WITH" in result
