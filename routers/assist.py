"""
AI Writing Assistant Router — context-aware field assistance.

POST /api/assist
  Accepts a "Field Context Package" describing the target field,
  its current value, neighboring fields, and the application section.
  Returns a professional rewrite or completion suggestion.

Optimized for **Qwen 3.5 9B** — leverages its superior instruction-
following (IFBench 76.5) to produce precise, tone-aware corporate text.
"""

from __future__ import annotations

import json
import time
import structlog

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from auth.dependencies import get_current_user
from auth.token_verify import VerifiedUser
from llm_client import generate, LLMClientError
from query_engine.pii_detector import scan_and_enforce, PIIAction, PIIDetectionError

logger = structlog.get_logger()

router = APIRouter()


# ── Request / Response models ────────────────────────────

class FieldContext(BaseModel):
    """Metadata about a neighboring field for additional context."""
    label: str = Field(..., max_length=200, description="Field label or name")
    value: str = Field(default="", max_length=2000, description="Current field value")


class AssistRequest(BaseModel):
    """The Field Context Package sent by the frontend."""
    field_label: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Label of the target field (e.g. 'Notes', 'Description')",
    )
    current_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="The user's current text or prompt in the field",
    )
    context: list[FieldContext] = Field(
        default_factory=list,
        max_length=10,
        description="Neighboring field labels and values for context",
    )
    app_section: str = Field(
        default="General",
        max_length=200,
        description="The current page/section (e.g. 'HR Leave Application')",
    )
    tone: str = Field(
        default="professional",
        description="Desired writing tone",
        pattern=r"^(professional|formal|friendly|concise|executive)$",
    )
    max_length: int | None = Field(
        default=None,
        ge=10,
        le=5000,
        description="Optional max character limit for the suggestion",
    )


class AssistResponse(BaseModel):
    """The AI writing suggestion returned to the frontend."""
    suggestion: str = Field(
        ...,
        description="The AI-generated professional text",
    )
    type: str = Field(
        ...,
        description="Whether this is a 'rewrite' of existing text or a 'completion' from a prompt",
    )
    tone: str = Field(
        ...,
        description="The tone that was applied",
    )
    metadata: AssistMetadata


class AssistMetadata(BaseModel):
    """Processing details for the suggestion."""
    execution_time_ms: float
    field_label: str
    app_section: str
    input_length: int
    output_length: int
    pii_redacted: bool = False


# ── System prompt template ───────────────────────────────

_SYSTEM_PROMPT = """You are the AI Orchestrator Writing Assistant, a specialized corporate text enhancement tool integrated into ATL Corp's internal business applications.

IDENTITY:
- You operate entirely within the company's secure local network — no data ever leaves the LAN.
- You are an expert at transforming informal, broken, or draft text into polished corporate communication.

CONTEXT FOR THIS REQUEST:
- Field: "{field_label}"
- Application Section: "{app_section}"
- Desired Tone: {tone}
{nearby_context}
TASK:
Analyze the user's input and determine the appropriate action:

1. **If the input is a PROMPT** (instructions like "write a...", "draft...", "notify about...", "compose..."):
   → Generate the requested content from scratch.
   → Set type = "completion"

2. **If the input is EXISTING TEXT** (broken, informal, or draft-quality text):
   → Rewrite it into professional, polished corporate text.
   → Preserve the original meaning and all factual details.
   → Set type = "rewrite"

WRITING RULES:
- Match the "{tone}" tone consistently.
- Maintain factual accuracy — never invent data or statistics not present in the input.
- Use proper grammar, punctuation, and professional vocabulary.
- If the field is "Subject" or a short-label field, keep the output concise (one line).
- If neighboring field data provides useful context (names, dates, departments), weave it naturally into the text.
{length_constraint}
OUTPUT FORMAT:
Respond with ONLY a JSON object (no markdown fences, no explanation):
{{"suggestion": "your polished text here", "type": "rewrite" or "completion"}}"""


def _build_nearby_context(context: list[FieldContext]) -> str:
    """Format neighboring field data for the system prompt."""
    if not context:
        return ""

    lines = ["- Neighboring Fields:"]
    for f in context[:5]:  # Cap at 5 nearby fields
        value_preview = f.value[:150] if f.value else "(empty)"
        lines.append(f'  • {f.label}: "{value_preview}"')

    return "\n".join(lines) + "\n"


def _build_system_prompt(request: AssistRequest) -> str:
    """Construct the full system prompt from the request context."""
    nearby = _build_nearby_context(request.context)
    length_constraint = ""
    if request.max_length:
        length_constraint = f"- Keep the output under {request.max_length} characters.\n"

    return _SYSTEM_PROMPT.format(
        field_label=request.field_label,
        app_section=request.app_section,
        tone=request.tone,
        nearby_context=nearby,
        length_constraint=length_constraint,
    )


# ── Endpoint ──────────────────────────────────────────────

@router.post(
    "/assist",
    response_model=AssistResponse,
    summary="AI Writing Assistant — context-aware field suggestions",
)
async def ai_assist(
    request: AssistRequest,
    user: VerifiedUser = Depends(get_current_user),
):
    """
    Transform user input into professional corporate text.

    The frontend sends a "Field Context Package" describing:
    - The target field (label + current text)
    - Neighboring fields for contextual awareness
    - The application section (e.g. "HR Leave Application")
    - Desired tone (professional, formal, friendly, concise, executive)

    The AI determines whether to **rewrite** existing text or **complete**
    a prompt, and returns a polished suggestion with metadata.
    """
    start_time = time.perf_counter()

    # ── PII scan (protect against leaking personal data) ─
    processed_text = request.current_text
    pii_redacted = False
    if settings.pii_detection_enabled:
        try:
            processed_text, pii_matches, blocked = scan_and_enforce(
                request.current_text, PIIAction.REDACT,
            )
            if pii_matches:
                pii_redacted = True
                logger.warning(
                    "assist_pii_redacted",
                    user_id=user.user_id,
                    pii_count=len(pii_matches),
                )
        except PIIDetectionError as exc:
            logger.warning(
                "assist_blocked_by_safety",
                user_id=user.user_id,
                reason=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

    # ── Build prompt ─────────────────────────────────────
    system_prompt = _build_system_prompt(request)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": processed_text},
    ]

    logger.info(
        "assist_request",
        user_id=user.user_id,
        field=request.field_label,
        section=request.app_section,
        tone=request.tone,
        input_len=len(request.current_text),
    )

    # ── Call LLM ─────────────────────────────────────────
    try:
        raw_response = await generate(messages, department=user.department, model=settings.llm_fast_model)
    except LLMClientError as exc:
        logger.error("assist_llm_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI model failed to generate suggestion: {exc}",
        )

    # ── Parse response ───────────────────────────────────
    suggestion, suggestion_type = _parse_llm_response(raw_response, request.current_text)

    # ── Apply max_length if specified ────────────────────
    if request.max_length and len(suggestion) > request.max_length:
        suggestion = suggestion[:request.max_length].rsplit(" ", 1)[0] + "…"

    total_ms = round((time.perf_counter() - start_time) * 1000, 2)

    logger.info(
        "assist_completed",
        user_id=user.user_id,
        type=suggestion_type,
        output_len=len(suggestion),
        time_ms=total_ms,
    )

    return AssistResponse(
        suggestion=suggestion,
        type=suggestion_type,
        tone=request.tone,
        metadata=AssistMetadata(
            execution_time_ms=total_ms,
            field_label=request.field_label,
            app_section=request.app_section,
            input_length=len(request.current_text),
            output_length=len(suggestion),
            pii_redacted=pii_redacted,
        ),
    )


def _parse_llm_response(raw: str, original_text: str) -> tuple[str, str]:
    """
    Parse the LLM's JSON response into (suggestion, type).

    Falls back gracefully if the LLM doesn't return valid JSON.
    """
    # Try to extract JSON from the response
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        suggestion = parsed.get("suggestion", "").strip()
        stype = parsed.get("type", "rewrite").strip().lower()

        if suggestion and stype in ("rewrite", "completion"):
            return suggestion, stype

        # Got JSON but missing/invalid fields — use the suggestion text if present
        if suggestion:
            return suggestion, _infer_type(original_text)

    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: use raw text as the suggestion
    # Strip common prefixes the LLM might add
    for prefix in ("suggestion:", "suggestion :", "here is", "here's"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip().strip('"').strip("'")
            break

    if text:
        return text, _infer_type(original_text)

    # Absolute fallback
    return original_text, "rewrite"


def _infer_type(original_text: str) -> str:
    """Infer whether the user input was a prompt or existing text."""
    prompt_markers = (
        "write ", "draft ", "compose ", "create ", "generate ",
        "make a ", "notify ", "inform ", "tell ", "prepare ",
        "summarize ", "describe ", "explain ", "list ",
    )
    lower = original_text.lower().strip()
    if any(lower.startswith(m) for m in prompt_markers):
        return "completion"
    return "rewrite"
