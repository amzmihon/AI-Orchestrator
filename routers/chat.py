"""
Chat endpoint — the core query pipeline (Qwen 3.5 enhanced).

POST /api/chat
  Supports three intent types:
    • text_processing  — direct LLM response (email polish, summarization, etc.)
    • data_query       — SQL generation → guardrail → execute → summarize
    • multi_step_analysis — chain-of-thought SQL + enhanced summarization

  Security & Guardrails:
    • PII detection — scans user input for sensitive data (LLM Guard inspired)
    • Topic blocking — canonical safety rules (NeMo Colang inspired)
    • Output validation — hallucination detection & quality checks
    • SQL refinement — iterative error correction with checkpoint validation

  Memory Integration:
    • Short-term — conversation history per session
    • Long-term — persistent preferences/facts via RAG
    • Episodic — past reasoning chains for learning & error avoidance
"""

from __future__ import annotations

import json
import time
import uuid
import decimal
import datetime
import structlog
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from auth.dependencies import get_current_user
from auth.token_verify import VerifiedUser
from rbac.permissions import get_allowed_tables, get_denied_columns
from query_engine.intent_classifier import (
    classify, Intent, episodic_memory, EpisodicEntry,
)
from query_engine.schema_loader import get_filtered_schema
from query_engine.prompt_builder import (
    build_sql_prompt,
    build_multi_step_sql_prompt,
    build_text_processing_prompt,
)
from query_engine.sql_guardrail import validate, extract_table_names, SQLGuardrailError
from query_engine.db_executor import execute, DBExecutionError
from query_engine.response_formatter import format_response
from query_engine.pii_detector import (
    scan_and_enforce, PIIAction, PIIDetectionError,
)
from query_engine.output_validator import (
    validate_sql_quality, validate_response_quality,
)
from query_engine.sql_refiner import refine_sql, classify_error
from data_freshness import get_data_freshness
from llm_client import generate, LLMClientError
from chat_history import (
    create_session,
    get_session,
    add_message,
    get_context_messages,
    update_session_title,
    retrieve_memories,
    extract_memories_from_exchange,
    store_episodic_memory,
    format_memories_for_prompt,
    generate_session_title,
)

logger = structlog.get_logger()

router = APIRouter()


# ── Request / Response models ────────────────────────────

class ChatRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        examples=["Show me the growth of the last 6 months"],
    )
    session_id: str | None = Field(
        default=None,
        description="Chat session ID. If omitted, a new session is created automatically.",
    )


class ChatResponseMetadata(BaseModel):
    execution_time_ms: float
    tables_accessed: list[str]
    user_role: str
    intent: str
    session_id: str
    memories_used: int = 0
    complexity_score: float = 0.0
    sub_tasks: list[str] = []
    sql_refinement_attempts: int = 0
    pii_redacted: bool = False
    quality_warnings: list[str] = []
    data_freshness: dict | None = None


class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None
    data: list[dict[str, Any]] | None = None
    metadata: ChatResponseMetadata


# ── Pipeline: Text Processing ────────────────────────────

async def _handle_text_processing(
    question: str,
    user: VerifiedUser,
    start_time: float,
    session_id: str,
    history_context: list[dict],
    memory_context: str,
    memories_used: int,
) -> ChatResponse:
    """Handle text-processing intents (email polish, summarization, etc.)."""
    messages = build_text_processing_prompt(question)

    # Inject memory context into system prompt
    if memory_context:
        messages[0]["content"] = memory_context + "\n\n" + messages[0]["content"]

    # Inject conversation history before the current user message
    if history_context:
        user_msg = messages.pop()  # Remove the user message
        messages.extend(history_context)
        messages.append(user_msg)

    try:
        answer = await generate(messages, department=user.department)
    except LLMClientError as exc:
        logger.error("llm_text_processing_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI model failed to process text: {exc}",
        )

    total_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return ChatResponse(
        answer=answer,
        sql=None,
        data=None,
        metadata=ChatResponseMetadata(
            execution_time_ms=total_ms,
            tables_accessed=[],
            user_role=user.role,
            intent="text_processing",
            session_id=session_id,
            memories_used=memories_used,
        ),
    )


# ── Pipeline: Data Query (standard) ─────────────────────

async def _handle_data_query(
    question: str,
    user: VerifiedUser,
    start_time: float,
    allowed_tables: list[str],
    denied_columns: dict,
    session_id: str,
    history_context: list[dict],
    memory_context: str,
    memories_used: int,
    complexity_score: float = 0.0,
    sub_tasks: list[str] | None = None,
) -> ChatResponse:
    """Handle standard data-query intents with iterative SQL refinement."""
    schema_text = get_filtered_schema(allowed_tables, denied_columns)

    # Enhance question with memory context for better SQL generation
    enhanced_question = question
    if memory_context:
        enhanced_question = memory_context + "\n\nUser Question: " + question

    messages = build_sql_prompt(schema_text, enhanced_question)

    # Inject conversation history so the LLM can resolve follow-up references
    if history_context:
        user_msg = messages.pop()
        messages.extend(history_context)
        messages.append(user_msg)

    try:
        raw_sql = await generate(messages, department=user.department)
    except LLMClientError as exc:
        logger.error("llm_sql_generation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI model failed to generate a query: {exc}",
        )

    logger.info("llm_sql_generated", sql=raw_sql[:200])

    # ── Detect LLM returning NO_DATA marker ────────────
    if raw_sql.strip().upper().startswith("NO_DATA"):
        # LLM determined the question is outside the database scope
        explanation = raw_sql.strip()
        if ":" in explanation:
            explanation = explanation.split(":", 1)[1].strip()
        else:
            explanation = (
                "I don't have data about that in our ERP system. "
                "I can help with orders, production, finance, inventory, "
                "HR, and logistics data for ATL Corp."
            )
        total_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ChatResponse(
            answer=explanation,
            sql=None,
            data=None,
            metadata=ChatResponseMetadata(
                execution_time_ms=total_ms,
                tables_accessed=[],
                user_role=user.role,
                intent="data_query",
                session_id=session_id,
                memories_used=memories_used,
            ),
        )

    try:
        validated_sql = validate(raw_sql, allowed_tables)
    except SQLGuardrailError as exc:
        logger.warning("sql_guardrail_blocked", raw_sql=raw_sql[:200], reason=str(exc))
        # Graceful fallback: explain the limitation instead of a raw error
        reason = str(exc)
        if "Access denied to table" in reason:
            total_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ChatResponse(
                answer=(
                    "I don't have data about that topic in our ERP database. "
                    "Our system contains data on: **Orders & Buyers**, **Production**, "
                    "**Finance** (invoices, payments), **Inventory**, **HR** (employees, "
                    "attendance), and **Logistics** (shipments). "
                    "Could you rephrase your question to relate to one of these areas?"
                ),
                sql=None,
                data=None,
                metadata=ChatResponseMetadata(
                    execution_time_ms=total_ms,
                    tables_accessed=[],
                    user_role=user.role,
                    intent="data_query",
                    session_id=session_id,
                    memories_used=memories_used,
                    quality_warnings=[f"Original query referenced unavailable data: {reason}"],
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Query rejected by safety filter: {exc}",
        )

    # ── SQL quality checks (soft warnings) ───────────────
    quality_issues = validate_sql_quality(validated_sql)
    quality_warnings = [i.message for i in quality_issues if i.severity.value == "warning"]
    if quality_warnings:
        logger.info("sql_quality_warnings", warnings=quality_warnings)

    # ── Execute with iterative refinement ────────────────
    refinement_attempts = 0
    try:
        rows, db_time_ms = await execute(validated_sql)
    except DBExecutionError as exc:
        # Try iterative SQL refinement
        if settings.max_sql_retries > 0:
            logger.info("sql_refinement_triggered", error=str(exc))
            try:
                validated_sql, attempts = await refine_sql(
                    question=question,
                    failed_sql=validated_sql,
                    error_message=str(exc),
                    schema_text=schema_text,
                    allowed_tables=allowed_tables,
                    department=user.department,
                )
                refinement_attempts = len(attempts)
                rows, db_time_ms = await execute(validated_sql)
                logger.info("sql_refinement_succeeded", attempts=refinement_attempts)
            except (LLMClientError, SQLGuardrailError, DBExecutionError) as refine_exc:
                logger.error("sql_refinement_failed", error=str(refine_exc))
                # Record failure in episodic memory
                if settings.episodic_memory_enabled:
                    error_type, _ = classify_error(str(exc))
                    episodic_memory.record(user.user_id, EpisodicEntry(
                        id=str(uuid.uuid4()),
                        timestamp=time.time(),
                        user_question=question,
                        classified_intent=Intent.DATA_QUERY.value,
                        confidence=0.0,
                        sub_tasks=sub_tasks or [],
                        sql_generated=validated_sql,
                        execution_success=False,
                        error_type=error_type,
                    ))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Database query failed after refinement: {refine_exc}",
                )
        else:
            logger.error("db_execution_failed", sql=validated_sql[:200], error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database query failed: {exc}",
            )

    logger.info("db_query_executed", row_count=len(rows), db_time_ms=db_time_ms)

    summary = await format_response(
        question=question,
        sql=validated_sql,
        data=rows,
        department=user.department,
        multi_step=False,
    )

    # ── Output quality validation ────────────────────────
    if settings.output_validation_enabled:
        response_issues = validate_response_quality(
            summary, "data_query", has_data=bool(rows),
        )
        response_warnings = [i.message for i in response_issues if i.severity.value == "warning"]
        quality_warnings.extend(response_warnings)

    # ── Record episodic memory ───────────────────────────
    if settings.episodic_memory_enabled:
        total_ms = round((time.perf_counter() - start_time) * 1000, 2)
        episodic_memory.record(user.user_id, EpisodicEntry(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            user_question=question,
            classified_intent=Intent.DATA_QUERY.value,
            confidence=1.0,
            sub_tasks=sub_tasks or [],
            sql_generated=validated_sql,
            execution_success=True,
            execution_time_ms=total_ms,
        ))

    total_ms = round((time.perf_counter() - start_time) * 1000, 2)
    tables_accessed = extract_table_names(validated_sql)

    return ChatResponse(
        answer=summary,
        sql=validated_sql,
        data=rows,
        metadata=ChatResponseMetadata(
            execution_time_ms=total_ms,
            tables_accessed=tables_accessed,
            user_role=user.role,
            intent="data_query",
            session_id=session_id,
            memories_used=memories_used,
            complexity_score=complexity_score,
            sub_tasks=sub_tasks or [],
            sql_refinement_attempts=refinement_attempts,
            quality_warnings=quality_warnings,
        ),
    )


# ── Pipeline: Multi-Step Analysis ────────────────────────

async def _handle_multi_step(
    question: str,
    user: VerifiedUser,
    start_time: float,
    allowed_tables: list[str],
    denied_columns: dict,
    session_id: str,
    history_context: list[dict],
    memory_context: str,
    memories_used: int,
    complexity_score: float = 0.0,
    sub_tasks: list[str] | None = None,
) -> ChatResponse:
    """Handle complex multi-step analysis with agentic chain-of-thought SQL."""
    schema_text = get_filtered_schema(allowed_tables, denied_columns)

    enhanced_question = question
    if memory_context:
        enhanced_question = memory_context + "\n\nUser Question: " + question

    # ── Gather episodic hints for the LLM ────────────────
    episodic_hints: list[str] = []
    if settings.episodic_memory_enabled:
        failure_patterns = episodic_memory.get_failure_patterns(user.user_id)
        if failure_patterns:
            episodic_hints.append(
                f"Avoid these past error patterns: {', '.join(failure_patterns)}"
            )
        similar = episodic_memory.get_similar_episodes(user.user_id, question, limit=3)
        for ep in similar:
            if ep.execution_success and ep.sql_generated:
                episodic_hints.append(
                    f"A similar query succeeded with approach: {ep.sql_generated[:100]}..."
                )

    messages = build_multi_step_sql_prompt(
        schema_text, enhanced_question,
        sub_tasks=sub_tasks,
        episodic_hints=episodic_hints,
    )

    # Inject conversation history for multi-turn context
    if history_context:
        user_msg = messages.pop()
        messages.extend(history_context)
        messages.append(user_msg)

    try:
        raw_sql = await generate(messages, department=user.department)
    except LLMClientError as exc:
        logger.error("llm_multi_step_sql_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI model failed to generate analysis query: {exc}",
        )

    logger.info("llm_multi_step_sql_generated", sql=raw_sql[:300])

    # ── Detect LLM returning NO_DATA marker ────────────
    if raw_sql.strip().upper().startswith("NO_DATA"):
        explanation = raw_sql.strip()
        if ":" in explanation:
            explanation = explanation.split(":", 1)[1].strip()
        else:
            explanation = (
                "I don't have data about that in our ERP system. "
                "I can help with orders, production, finance, inventory, "
                "HR, and logistics data for ATL Corp."
            )
        total_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ChatResponse(
            answer=explanation,
            sql=None,
            data=None,
            metadata=ChatResponseMetadata(
                execution_time_ms=total_ms,
                tables_accessed=[],
                user_role=user.role,
                intent="multi_step_analysis",
                session_id=session_id,
                memories_used=memories_used,
            ),
        )

    try:
        validated_sql = validate(raw_sql, allowed_tables)
    except SQLGuardrailError as exc:
        logger.warning("sql_guardrail_blocked", raw_sql=raw_sql[:300], reason=str(exc))
        reason = str(exc)
        if "Access denied to table" in reason:
            total_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ChatResponse(
                answer=(
                    "I don't have data about that topic in our ERP database. "
                    "Our system contains data on: **Orders & Buyers**, **Production**, "
                    "**Finance** (invoices, payments), **Inventory**, **HR** (employees, "
                    "attendance), and **Logistics** (shipments). "
                    "Could you rephrase your question to relate to one of these areas?"
                ),
                sql=None,
                data=None,
                metadata=ChatResponseMetadata(
                    execution_time_ms=total_ms,
                    tables_accessed=[],
                    user_role=user.role,
                    intent="multi_step_analysis",
                    session_id=session_id,
                    memories_used=memories_used,
                    quality_warnings=[f"Original query referenced unavailable data: {reason}"],
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Query rejected by safety filter: {exc}",
        )

    # ── SQL quality checks ───────────────────────────────
    quality_issues = validate_sql_quality(validated_sql)
    quality_warnings = [i.message for i in quality_issues if i.severity.value == "warning"]

    # ── Execute with iterative refinement ────────────────
    refinement_attempts = 0
    try:
        rows, db_time_ms = await execute(validated_sql)
    except DBExecutionError as exc:
        if settings.max_sql_retries > 0:
            logger.info("multi_step_sql_refinement_triggered", error=str(exc))
            try:
                validated_sql, attempts = await refine_sql(
                    question=question,
                    failed_sql=validated_sql,
                    error_message=str(exc),
                    schema_text=schema_text,
                    allowed_tables=allowed_tables,
                    department=user.department,
                )
                refinement_attempts = len(attempts)
                rows, db_time_ms = await execute(validated_sql)
            except (LLMClientError, SQLGuardrailError, DBExecutionError) as refine_exc:
                logger.error("multi_step_refinement_failed", error=str(refine_exc))
                if settings.episodic_memory_enabled:
                    error_type, _ = classify_error(str(exc))
                    episodic_memory.record(user.user_id, EpisodicEntry(
                        id=str(uuid.uuid4()),
                        timestamp=time.time(),
                        user_question=question,
                        classified_intent=Intent.MULTI_STEP.value,
                        confidence=0.0,
                        sub_tasks=sub_tasks or [],
                        sql_generated=validated_sql,
                        execution_success=False,
                        error_type=error_type,
                    ))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Analysis query failed after refinement: {refine_exc}",
                )
        else:
            logger.error("db_execution_failed", sql=validated_sql[:300], error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database query failed: {exc}",
            )

    logger.info("db_multi_step_executed", row_count=len(rows), db_time_ms=db_time_ms)

    # Use enhanced multi-step summarization
    summary = await format_response(
        question=question,
        sql=validated_sql,
        data=rows,
        department=user.department,
        multi_step=True,
    )

    # ── Output quality validation ────────────────────────
    if settings.output_validation_enabled:
        response_issues = validate_response_quality(
            summary, "multi_step_analysis", has_data=bool(rows),
        )
        response_warnings = [i.message for i in response_issues if i.severity.value == "warning"]
        quality_warnings.extend(response_warnings)

    # ── Record episodic memory ───────────────────────────
    if settings.episodic_memory_enabled:
        total_ms = round((time.perf_counter() - start_time) * 1000, 2)
        episodic_memory.record(user.user_id, EpisodicEntry(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            user_question=question,
            classified_intent=Intent.MULTI_STEP.value,
            confidence=1.0,
            sub_tasks=sub_tasks or [],
            sql_generated=validated_sql,
            execution_success=True,
            execution_time_ms=total_ms,
        ))

    total_ms = round((time.perf_counter() - start_time) * 1000, 2)
    tables_accessed = extract_table_names(validated_sql)

    return ChatResponse(
        answer=summary,
        sql=validated_sql,
        data=rows,
        metadata=ChatResponseMetadata(
            execution_time_ms=total_ms,
            tables_accessed=tables_accessed,
            user_role=user.role,
            intent="multi_step_analysis",
            session_id=session_id,
            memories_used=memories_used,
            complexity_score=complexity_score,
            sub_tasks=sub_tasks or [],
            sql_refinement_attempts=refinement_attempts,
            quality_warnings=quality_warnings,
        ),
    )


# ── Main chat endpoint ───────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: VerifiedUser = Depends(get_current_user),
):
    """
    Process a natural-language question through the full AI pipeline.

    The question is first classified into one of three intents:
      • text_processing — direct LLM response (no database)
      • data_query — SQL generation + execution + summarization
      • multi_step_analysis — chain-of-thought SQL + enhanced analysis

    Session & Memory:
      • If session_id is provided, conversation history is loaded and injected.
      • If session_id is omitted, a new session is auto-created.
      • Long-term memories are retrieved and injected into the prompt.
      • User + assistant messages are persisted for future context.
      • Preferences/facts are auto-extracted and saved as long-term memory.
    """
    start_time = time.perf_counter()

    # ── 0. PII & Safety Scan ────────────────────────────
    processed_question = request.question
    pii_redacted = False
    if settings.pii_detection_enabled:
        try:
            processed_question, pii_matches, blocked = scan_and_enforce(
                request.question, PIIAction.REDACT,
            )
            if pii_matches:
                pii_redacted = True
                pii_types = ", ".join(set(m.pii_type for m in pii_matches))
                logger.warning(
                    "pii_detected_and_redacted",
                    user_id=user.user_id,
                    pii_types=pii_types,
                    count=len(pii_matches),
                )
        except PIIDetectionError as exc:
            logger.warning("request_blocked_by_safety", user_id=user.user_id, reason=str(exc))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

    # ── 0a. Session management ──────────────────────────
    session_id = request.session_id
    if session_id:
        session = await get_session(session_id, user.user_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or does not belong to you.",
            )
    else:
        # Auto-create a new session
        title = generate_session_title(processed_question)
        session = await create_session(user.user_id, title=title)
        session_id = session.id
        logger.info("auto_created_session", session_id=session_id, title=title)

    # ── 0b. Load conversation history ───────────────────
    history_context = await get_context_messages(session_id)

    # ── 0c. Retrieve relevant long-term memories (RAG) ──
    memories = await retrieve_memories(user.user_id, processed_question)
    memory_context = format_memories_for_prompt(memories)
    memories_used = len(memories)

    if memories_used:
        logger.info("memories_injected", count=memories_used, user_id=user.user_id)

    # ── 0d. Save user message to session ────────────────
    await add_message(
        session_id=session_id,
        role="user",
        content=request.question,  # Store original (unredacted) for history
    )

    # ── 0e. Auto-set title from first message ───────────
    if session.message_count == 0 and not request.session_id:
        pass
    elif session.message_count == 0 and request.session_id:
        title = generate_session_title(processed_question)
        await update_session_title(session_id, user.user_id, title)

    # ── 1. Classify intent (with episodic memory) ───────
    classified = classify(processed_question, user_id=user.user_id)

    logger.info(
        "chat_request",
        user_id=user.user_id,
        role=user.role,
        session_id=session_id,
        intent=classified.intent.value,
        intent_confidence=classified.confidence,
        intent_reason=classified.reasoning,
        complexity=classified.complexity_score,
        sub_tasks=classified.sub_tasks,
        suggested_tables=classified.suggested_tables,
        pii_redacted=pii_redacted,
        question=processed_question[:120],
    )

    # ── 2. Route to pipeline ────────────────────────────
    if classified.intent == Intent.TEXT_PROCESSING:
        response = await _handle_text_processing(
            processed_question, user, start_time,
            session_id, history_context, memory_context, memories_used,
        )
        # Add enhanced metadata
        response.metadata.complexity_score = classified.complexity_score
        response.metadata.sub_tasks = classified.sub_tasks
        response.metadata.pii_redacted = pii_redacted
    else:
        # Data intents require RBAC check
        allowed_tables = get_allowed_tables(user.role)
        if not allowed_tables:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your role '{user.role}' does not have access to any data. "
                    "Contact your administrator."
                ),
            )
        denied_columns = get_denied_columns(user.role)

        if classified.intent == Intent.MULTI_STEP:
            response = await _handle_multi_step(
                processed_question, user, start_time,
                allowed_tables, denied_columns,
                session_id, history_context, memory_context, memories_used,
                complexity_score=classified.complexity_score,
                sub_tasks=classified.sub_tasks,
            )
        else:
            response = await _handle_data_query(
                processed_question, user, start_time,
                allowed_tables, denied_columns,
                session_id, history_context, memory_context, memories_used,
                complexity_score=classified.complexity_score,
                sub_tasks=classified.sub_tasks,
            )
        response.metadata.pii_redacted = pii_redacted

    # ── 3. Save assistant response to session ───────────
    data_summary = None
    if response.data:
        def _default(obj):
            if isinstance(obj, decimal.Decimal):
                return float(obj)
            if isinstance(obj, (datetime.date, datetime.datetime)):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
        data_summary = json.dumps(response.data[:5], default=_default)

    await add_message(
        session_id=session_id,
        role="assistant",
        content=response.answer,
        sql=response.sql,
        data_summary=data_summary,
        intent=classified.intent.value,
    )

    # ── 4. Auto-extract long-term memories ──────────────
    try:
        await extract_memories_from_exchange(
            user_id=user.user_id,
            user_message=processed_question,
            assistant_response=response.answer,
        )
    except Exception as exc:
        logger.warning("memory_extraction_failed", error=str(exc))

    # ── 4b. Store episodic memory (reasoning chain) ─────
    try:
        exec_time = (time.perf_counter() - start_time) * 1000
        has_error = bool(response.metadata.quality_warnings)
        await store_episodic_memory(
            user_id=user.user_id,
            question=processed_question,
            classified_intent=classified.intent.value,
            confidence=classified.confidence,
            sub_tasks=classified.sub_tasks,
            sql_generated=response.sql,
            execution_success=not has_error,
            error_type=response.metadata.quality_warnings[0] if has_error else None,
            execution_time_ms=exec_time,
        )
    except Exception as exc:
        logger.warning("episodic_memory_failed", error=str(exc))

    # ── 5. Inject data freshness into metadata ──────────
    if response.metadata.intent != "text_processing":
        try:
            freshness = await get_data_freshness()
            response.metadata.data_freshness = {
                "status": freshness["status"],
                "message": freshness["message"],
                "minutes_ago": freshness["minutes_ago"],
            }
        except Exception as exc:
            logger.debug("freshness_check_failed", error=str(exc))

    return response
