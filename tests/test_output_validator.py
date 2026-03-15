"""
Tests for the Output Validator module.
"""

import pytest
from query_engine.output_validator import (
    validate_sql_quality,
    validate_response_quality,
    validate_json_output,
    ValidationSeverity,
)


class TestSQLQualityValidation:
    def test_select_star_warning(self):
        issues = validate_sql_quality("SELECT * FROM employees")
        warnings = [i for i in issues if i.code == "SELECT\\S+\\*"]
        # Should flag SELECT *
        assert any("SELECT *" in i.message for i in issues)

    def test_no_limit_warning(self):
        issues = validate_sql_quality("SELECT name FROM employees")
        assert any(i.code == "NO_LIMIT" for i in issues)

    def test_with_limit_no_warning(self):
        issues = validate_sql_quality("SELECT name FROM employees LIMIT 50")
        assert not any(i.code == "NO_LIMIT" for i in issues)

    def test_high_limit_warning(self):
        issues = validate_sql_quality("SELECT name FROM employees LIMIT 50000")
        assert any(i.code == "HIGH_LIMIT" for i in issues)

    def test_clean_query_minimal_issues(self):
        sql = "SELECT e.name, e.department FROM employees e WHERE e.active = true LIMIT 100"
        issues = validate_sql_quality(sql)
        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        assert len(errors) == 0


class TestResponseQualityValidation:
    def test_hallucination_marker_detected(self):
        response = "As an AI language model, I cannot access the database directly."
        issues = validate_response_quality(response, "data_query", has_data=True)
        assert any(i.code == "HALLUCINATION_MARKER" for i in issues)

    def test_short_response_warning(self):
        issues = validate_response_quality("OK", "data_query", has_data=True)
        assert any(i.code == "SHORT_RESPONSE" for i in issues)

    def test_normal_response_no_issues(self):
        response = (
            "Based on the query results, total revenue for Q1 was **$2.4M**, "
            "representing a 15% increase compared to the same period last year. "
            "The Engineering department contributed the most at $800K."
        )
        issues = validate_response_quality(response, "data_query", has_data=True)
        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        assert len(errors) == 0

    def test_text_processing_no_short_warning(self):
        issues = validate_response_quality("Done.", "text_processing", has_data=False)
        # Short response warning should NOT fire for text_processing
        assert not any(i.code == "SHORT_RESPONSE" for i in issues)


class TestJSONOutputValidation:
    def test_valid_json(self):
        parsed, issues = validate_json_output('{"name": "test", "value": 42}')
        assert parsed is not None
        assert parsed["name"] == "test"
        assert len(issues) == 0

    def test_invalid_json(self):
        parsed, issues = validate_json_output("not valid json")
        assert parsed is None
        assert any(i.code == "INVALID_JSON" for i in issues)

    def test_json_in_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        parsed, issues = validate_json_output(text)
        assert parsed is not None
        assert parsed["key"] == "value"

    def test_missing_required_keys(self):
        parsed, issues = validate_json_output(
            '{"name": "test"}',
            required_keys=["name", "status", "priority"],
        )
        assert parsed is not None
        assert any(i.code == "MISSING_KEYS" for i in issues)
        missing_issue = next(i for i in issues if i.code == "MISSING_KEYS")
        assert "status" in missing_issue.message
        assert "priority" in missing_issue.message

    def test_all_required_keys_present(self):
        parsed, issues = validate_json_output(
            '{"name": "test", "status": "active"}',
            required_keys=["name", "status"],
        )
        assert parsed is not None
        assert not any(i.code == "MISSING_KEYS" for i in issues)
