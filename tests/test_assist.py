"""
Tests for the AI Writing Assistant router.

Covers:
 - Request/response model validation
 - _parse_llm_response (JSON, markdown fences, fallback)
 - _infer_type (prompt markers)
 - _build_nearby_context (neighboring fields)
 - _build_system_prompt (template rendering)
 - max_length truncation
 - PII redaction handling (mocked)
 - Endpoint integration (mocked LLM + auth)
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from pydantic import ValidationError

from routers.assist import (
    AssistRequest,
    AssistResponse,
    AssistMetadata,
    FieldContext,
    _parse_llm_response,
    _infer_type,
    _build_nearby_context,
    _build_system_prompt,
)


# ═══════════════════════════════════════════════════
#  Model Validation
# ═══════════════════════════════════════════════════

class TestAssistRequestValidation:
    """AssistRequest Pydantic validation tests."""

    def test_minimal_valid_request(self):
        req = AssistRequest(field_label="Notes", current_text="need day off")
        assert req.field_label == "Notes"
        assert req.tone == "professional"
        assert req.app_section == "General"
        assert req.context == []
        assert req.max_length is None

    def test_full_request(self):
        req = AssistRequest(
            field_label="Description",
            current_text="proj delayed shipment issue",
            context=[FieldContext(label="Project", value="Alpha")],
            app_section="Project Management",
            tone="executive",
            max_length=200,
        )
        assert req.tone == "executive"
        assert req.max_length == 200
        assert len(req.context) == 1

    def test_empty_field_label_rejected(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="", current_text="some text")

    def test_empty_current_text_rejected(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="Notes", current_text="")

    def test_invalid_tone_rejected(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="Notes", current_text="hi", tone="casual")

    def test_all_valid_tones(self):
        for tone in ("professional", "formal", "friendly", "concise", "executive"):
            req = AssistRequest(field_label="X", current_text="Y", tone=tone)
            assert req.tone == tone

    def test_max_length_too_small(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="X", current_text="Y", max_length=5)

    def test_max_length_too_large(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="X", current_text="Y", max_length=10000)

    def test_field_label_max_length(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="X" * 201, current_text="Y")

    def test_current_text_max_length(self):
        with pytest.raises(ValidationError):
            AssistRequest(field_label="X", current_text="Y" * 5001)


class TestFieldContextValidation:
    """FieldContext model tests."""

    def test_valid_field_context(self):
        fc = FieldContext(label="Name", value="Ahmed")
        assert fc.label == "Name"
        assert fc.value == "Ahmed"

    def test_empty_value_allowed(self):
        fc = FieldContext(label="Name")
        assert fc.value == ""

    def test_label_required(self):
        with pytest.raises(ValidationError):
            FieldContext(value="test")


# ═══════════════════════════════════════════════════
#  _parse_llm_response
# ═══════════════════════════════════════════════════

class TestParseLLMResponse:
    """Tests for JSON and fallback parsing of LLM output."""

    def test_valid_json_rewrite(self):
        raw = '{"suggestion": "This is polished text.", "type": "rewrite"}'
        suggestion, stype = _parse_llm_response(raw, "original draft")
        assert suggestion == "This is polished text."
        assert stype == "rewrite"

    def test_valid_json_completion(self):
        raw = '{"suggestion": "Dear Team, ...", "type": "completion"}'
        suggestion, stype = _parse_llm_response(raw, "write an email")
        assert suggestion == "Dear Team, ..."
        assert stype == "completion"

    def test_markdown_fences_stripped(self):
        raw = '```json\n{"suggestion": "Clean text.", "type": "rewrite"}\n```'
        suggestion, stype = _parse_llm_response(raw, "messy draft")
        assert suggestion == "Clean text."
        assert stype == "rewrite"

    def test_plain_text_fallback(self):
        raw = "This is just a plain suggestion without JSON."
        suggestion, stype = _parse_llm_response(raw, "fix my text")
        assert suggestion == raw
        assert stype == "rewrite"

    def test_plain_text_with_prompt_start(self):
        raw = "A well-structured email notification."
        suggestion, stype = _parse_llm_response(raw, "write a reminder email")
        assert suggestion == raw
        assert stype == "completion"

    def test_prefixed_fallback(self):
        raw = 'Suggestion: "The project has been delayed."'
        suggestion, stype = _parse_llm_response(raw, "proj delayed")
        assert "project" in suggestion.lower()
        assert stype == "rewrite"

    def test_json_missing_type_uses_default(self):
        raw = '{"suggestion": "Clean text here"}'
        suggestion, stype = _parse_llm_response(raw, "draft an email")
        assert suggestion == "Clean text here"
        # get("type", "rewrite") returns "rewrite" which is valid
        assert stype == "rewrite"

    def test_json_invalid_type_uses_inference(self):
        raw = '{"suggestion": "Some text", "type": "unknown"}'
        suggestion, stype = _parse_llm_response(raw, "broken draft")
        assert suggestion == "Some text"
        assert stype == "rewrite"

    def test_empty_response_returns_original(self):
        raw = ""
        suggestion, stype = _parse_llm_response(raw, "my original text")
        assert suggestion == "my original text"
        assert stype == "rewrite"

    def test_whitespace_only_returns_original(self):
        raw = "   \n  \t  "
        suggestion, stype = _parse_llm_response(raw, "input text")
        assert suggestion == "input text"
        assert stype == "rewrite"

    def test_json_empty_suggestion_falls_through(self):
        raw = '{"suggestion": "", "type": "rewrite"}'
        suggestion, stype = _parse_llm_response(raw, "original")
        # Empty suggestion is falsy, falls through JSON parse to raw text fallback
        # Raw text is the JSON string itself, which is returned as-is
        assert suggestion  # Non-empty (the raw JSON string)
        assert stype == "rewrite"


# ═══════════════════════════════════════════════════
#  _infer_type
# ═══════════════════════════════════════════════════

class TestInferType:
    """Tests for prompt vs. existing text classification."""

    @pytest.mark.parametrize("text,expected", [
        ("write a summary of Q3 results", "completion"),
        ("draft an email to the team", "completion"),
        ("compose a professional message", "completion"),
        ("create a project status update", "completion"),
        ("generate a report summary", "completion"),
        ("notify the HR department about leave", "completion"),
        ("summarize the meeting notes", "completion"),
        ("describe the project timeline", "completion"),
        ("list all pending tasks", "completion"),
        ("prepare a budget overview", "completion"),
    ])
    def test_prompt_markers_detected(self, text, expected):
        assert _infer_type(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("need day off next week", "rewrite"),
        ("proj behind schedule vendor issues", "rewrite"),
        ("sales were good we beat target", "rewrite"),
        ("meeting went fine discussed budget", "rewrite"),
        ("the employee has been absent", "rewrite"),
        ("Q3 revenue exceeded expectations", "rewrite"),
    ])
    def test_regular_text_classified_as_rewrite(self, text, expected):
        assert _infer_type(text) == expected

    def test_case_insensitive(self):
        assert _infer_type("Write a memo about the policy") == "completion"
        assert _infer_type("DRAFT a notice") == "completion"

    def test_leading_whitespace_handled(self):
        assert _infer_type("  write a note") == "completion"
        assert _infer_type("  broken draft here") == "rewrite"


# ═══════════════════════════════════════════════════
#  _build_nearby_context
# ═══════════════════════════════════════════════════

class TestBuildNearbyContext:
    """Tests for neighboring field context construction."""

    def test_empty_context_returns_empty(self):
        result = _build_nearby_context([])
        assert result == ""

    def test_single_field(self):
        ctx = [FieldContext(label="Employee", value="Ahmed Rashid")]
        result = _build_nearby_context(ctx)
        assert "Neighboring Fields:" in result
        assert "Employee" in result
        assert "Ahmed Rashid" in result

    def test_multiple_fields(self):
        ctx = [
            FieldContext(label="Name", value="Ahmed"),
            FieldContext(label="Dept", value="Engineering"),
            FieldContext(label="Date", value="2025-01-15"),
        ]
        result = _build_nearby_context(ctx)
        assert "Name" in result
        assert "Dept" in result
        assert "Date" in result

    def test_max_five_fields(self):
        ctx = [FieldContext(label=f"Field{i}", value=f"Val{i}") for i in range(10)]
        result = _build_nearby_context(ctx)
        assert "Field4" in result
        assert "Field5" not in result  # 0-indexed, so cap at 5 means indices 0-4

    def test_empty_value_shows_placeholder(self):
        ctx = [FieldContext(label="Notes", value="")]
        result = _build_nearby_context(ctx)
        assert "(empty)" in result

    def test_long_value_truncated(self):
        ctx = [FieldContext(label="Long", value="X" * 300)]
        result = _build_nearby_context(ctx)
        assert len(result) < 300 + 100  # value truncated to 150 + overhead


# ═══════════════════════════════════════════════════
#  _build_system_prompt
# ═══════════════════════════════════════════════════

class TestBuildSystemPrompt:
    """Tests for system prompt template rendering."""

    def test_basic_prompt_rendering(self):
        req = AssistRequest(
            field_label="Notes",
            current_text="test input",
            tone="professional",
            app_section="HR Dashboard",
        )
        prompt = _build_system_prompt(req)
        assert 'Field: "Notes"' in prompt
        assert "HR Dashboard" in prompt
        assert "professional" in prompt
        assert "AI Orchestrator Writing Assistant" in prompt

    def test_with_max_length(self):
        req = AssistRequest(
            field_label="Subject",
            current_text="test",
            max_length=100,
        )
        prompt = _build_system_prompt(req)
        assert "100 characters" in prompt

    def test_without_max_length(self):
        req = AssistRequest(
            field_label="Description",
            current_text="test",
        )
        prompt = _build_system_prompt(req)
        assert "characters" not in prompt.split("OUTPUT FORMAT")[0]  # No length constraint before output section

    def test_with_context_fields(self):
        req = AssistRequest(
            field_label="Notes",
            current_text="test",
            context=[
                FieldContext(label="Project", value="Alpha"),
                FieldContext(label="Manager", value="Sarah"),
            ],
        )
        prompt = _build_system_prompt(req)
        assert "Project" in prompt
        assert "Alpha" in prompt
        assert "Manager" in prompt

    def test_executive_tone(self):
        req = AssistRequest(
            field_label="Summary",
            current_text="test",
            tone="executive",
        )
        prompt = _build_system_prompt(req)
        assert "executive" in prompt

    def test_json_output_instruction(self):
        req = AssistRequest(field_label="X", current_text="Y")
        prompt = _build_system_prompt(req)
        assert "JSON" in prompt
        assert '"suggestion"' in prompt


# ═══════════════════════════════════════════════════
#  Endpoint Integration (mocked LLM + auth)
# ═══════════════════════════════════════════════════

@pytest.fixture
def mock_user():
    """A fake VerifiedUser for dependency override."""
    from auth.token_verify import VerifiedUser

    return VerifiedUser(
        user_id=1,
        username="ahmed.rashid@atlcorp.com",
        role="admin",
        department="Engineering",
        permissions=["query_all_departments"],
    )


@pytest.fixture
def test_client(mock_user):
    """FastAPI TestClient with mocked auth dependency."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from auth.dependencies import get_current_user
    from routers.assist import router

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[get_current_user] = lambda: mock_user

    return TestClient(app)


class TestAssistEndpoint:
    """Integration tests for POST /api/assist."""

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_successful_rewrite(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = '{"suggestion": "I would like to request leave.", "type": "rewrite"}'

        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
            "current_text": "need day off",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestion"] == "I would like to request leave."
        assert data["type"] == "rewrite"
        assert data["tone"] == "professional"
        assert data["metadata"]["field_label"] == "Notes"
        assert data["metadata"]["input_length"] == len("need day off")

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_successful_completion(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = '{"suggestion": "Dear Team, please be advised...", "type": "completion"}'

        resp = test_client.post("/api/assist", json={
            "field_label": "Body",
            "current_text": "write an email about policy update",
            "tone": "formal",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "completion"
        assert data["tone"] == "formal"

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_with_context_fields(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = '{"suggestion": "Project Alpha status update.", "type": "rewrite"}'

        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
            "current_text": "proj update alpha things going well",
            "context": [
                {"label": "Project", "value": "Alpha"},
                {"label": "Manager", "value": "Sarah"},
            ],
            "app_section": "Project Management",
        })
        assert resp.status_code == 200

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_max_length_truncation(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        long_suggestion = "A" * 300
        mock_generate.return_value = json.dumps({
            "suggestion": long_suggestion,
            "type": "rewrite",
        })

        resp = test_client.post("/api/assist", json={
            "field_label": "Subject",
            "current_text": "something",
            "max_length": 50,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["suggestion"]) <= 51  # 50 + "…"

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_pii_redaction(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = True
        mock_generate.return_value = '{"suggestion": "Redacted text here.", "type": "rewrite"}'

        with patch("routers.assist.scan_and_enforce") as mock_pii:
            mock_pii.return_value = ("redacted text", [{"type": "email"}], False)

            resp = test_client.post("/api/assist", json={
                "field_label": "Notes",
                "current_text": "contact john@email.com about it",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["metadata"]["pii_redacted"] is True

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_llm_failure_returns_502(self, mock_settings, mock_generate, test_client):
        from llm_client import LLMClientError
        mock_settings.pii_detection_enabled = False
        mock_generate.side_effect = LLMClientError("Model unavailable")

        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
            "current_text": "fix this text",
        })
        assert resp.status_code == 502
        assert "AI model failed" in resp.json()["detail"]

    def test_missing_field_label_returns_422(self, test_client):
        resp = test_client.post("/api/assist", json={
            "current_text": "some text",
        })
        assert resp.status_code == 422

    def test_missing_current_text_returns_422(self, test_client):
        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
        })
        assert resp.status_code == 422

    def test_invalid_tone_returns_422(self, test_client):
        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
            "current_text": "some text",
            "tone": "sarcastic",
        })
        assert resp.status_code == 422

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_metadata_structure(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = '{"suggestion": "Done.", "type": "rewrite"}'

        resp = test_client.post("/api/assist", json={
            "field_label": "Summary",
            "current_text": "test input here",
            "app_section": "Reports",
            "tone": "concise",
        })
        assert resp.status_code == 200
        meta = resp.json()["metadata"]
        assert meta["field_label"] == "Summary"
        assert meta["app_section"] == "Reports"
        assert meta["input_length"] == len("test input here")
        assert meta["output_length"] == len("Done.")
        assert isinstance(meta["execution_time_ms"], (int, float))
        assert meta["pii_redacted"] is False

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_executive_tone(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = '{"suggestion": "Executive summary: Q3 exceeded targets.", "type": "rewrite"}'

        resp = test_client.post("/api/assist", json={
            "field_label": "Executive Summary",
            "current_text": "Q3 was good",
            "tone": "executive",
        })
        assert resp.status_code == 200
        assert resp.json()["tone"] == "executive"

    @patch("routers.assist.generate")
    @patch("routers.assist.settings")
    def test_fallback_on_non_json_response(self, mock_settings, mock_generate, test_client):
        mock_settings.pii_detection_enabled = False
        mock_generate.return_value = "This is just plain text from the LLM."

        resp = test_client.post("/api/assist", json={
            "field_label": "Notes",
            "current_text": "rough draft here",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestion"] == "This is just plain text from the LLM."
        assert data["type"] == "rewrite"
