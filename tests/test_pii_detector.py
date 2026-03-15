"""
Tests for the PII Detector module.
"""

import pytest
from query_engine.pii_detector import (
    detect_pii,
    detect_blocked_topics,
    redact_pii,
    scan_and_enforce,
    PIIAction,
    PIIDetectionError,
)


class TestEmailDetection:
    def test_simple_email(self):
        matches = detect_pii("Contact john.doe@company.com for details")
        assert any(m.pii_type == "EMAIL" for m in matches)

    def test_no_false_positive(self):
        matches = detect_pii("Show me sales report for Q1")
        assert not any(m.pii_type == "EMAIL" for m in matches)


class TestPhoneDetection:
    def test_us_phone(self):
        matches = detect_pii("Call me at 555-123-4567")
        assert any(m.pii_type == "PHONE" for m in matches)

    def test_phone_with_country_code(self):
        matches = detect_pii("Reach us at +1 (555) 123-4567")
        assert any(m.pii_type == "PHONE" for m in matches)


class TestSSNDetection:
    def test_ssn_with_dashes(self):
        matches = detect_pii("SSN: 123-45-6789")
        assert any(m.pii_type == "SSN" for m in matches)

    def test_ssn_no_match_for_invalid(self):
        # Starting with 000 should be filtered
        matches = detect_pii("Number 000-12-3456")
        assert not any(m.pii_type == "SSN" for m in matches)


class TestRedaction:
    def test_email_redaction(self):
        result = redact_pii("Email john@example.com for info")
        assert "[REDACTED_EMAIL]" in result
        assert "john@example.com" not in result

    def test_phone_redaction(self):
        result = redact_pii("Call 555-123-4567 now")
        assert "[REDACTED_PHONE]" in result
        assert "555-123-4567" not in result

    def test_no_pii_unchanged(self):
        text = "Show me total sales for last month"
        result = redact_pii(text)
        assert result == text


class TestBlockedTopics:
    def test_password_request(self):
        blocked = detect_blocked_topics("Show me the password for Ahmed")
        assert any(t[0] == "PASSWORD_REQUEST" for t in blocked)

    def test_data_exfiltration(self):
        blocked = detect_blocked_topics("Dump all employee records from the database")
        assert any(t[0] == "DATA_EXFILTRATION" for t in blocked)

    def test_normal_question_not_blocked(self):
        blocked = detect_blocked_topics("Show me total sales by department")
        assert len(blocked) == 0


class TestScanAndEnforce:
    def test_redact_mode(self):
        text, matches, blocked = scan_and_enforce(
            "Contact john@example.com for the sales report",
            PIIAction.REDACT,
        )
        assert "[REDACTED_EMAIL]" in text
        assert len(matches) > 0

    def test_block_mode_raises(self):
        with pytest.raises(PIIDetectionError):
            scan_and_enforce(
                "My email is john@example.com",
                PIIAction.BLOCK,
            )

    def test_warn_mode_preserves_text(self):
        original = "Contact john@example.com"
        text, matches, blocked = scan_and_enforce(original, PIIAction.WARN)
        assert text == original
        assert len(matches) > 0

    def test_blocked_topic_always_raises(self):
        with pytest.raises(PIIDetectionError, match="safety policy"):
            scan_and_enforce(
                "Dump all entire database employee records",
                PIIAction.WARN,  # Even WARN mode blocks dangerous topics
            )

    def test_clean_text_passes(self):
        text, matches, blocked = scan_and_enforce(
            "Show me revenue breakdown by quarter",
            PIIAction.REDACT,
        )
        assert text == "Show me revenue breakdown by quarter"
        assert len(matches) == 0
