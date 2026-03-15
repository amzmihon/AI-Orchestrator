"""
Response Formatter — sends raw query results back to the LLM
to produce a professional, human-readable summary.

Supports two modes:
  • Standard — concise executive summary
  • Multi-step — detailed analytical response with step-by-step reasoning
"""

from __future__ import annotations

from typing import Any

from llm_client import generate, LLMClientError
from query_engine.prompt_builder import build_summary_prompt, build_multi_step_summary_prompt


async def format_response(
    question: str,
    sql: str,
    data: list[dict[str, Any]],
    department: str | None = None,
    multi_step: bool = False,
) -> str:
    """
    Take raw query results and produce a professional summary
    by calling the LLM a second time.

    Args:
        question: The user's original question.
        sql: The SQL that was executed.
        data: The result rows.
        department: User's department (for per-department LLM key routing).
        multi_step: If True, use the enhanced multi-step summary prompt.

    Returns:
        A human-readable summary string.
    """
    # If no data, return a standard message without calling LLM
    if not data:
        return (
            "No matching records were found for your query. "
            "Please try rephrasing your question or check that the "
            "relevant data exists in the system."
        )

    if multi_step:
        messages = build_multi_step_summary_prompt(
            question=question, sql=sql, data=data,
        )
    else:
        messages = build_summary_prompt(question=question, sql=sql, data=data)

    try:
        summary = await generate(messages, department=department)
    except LLMClientError:
        # Fallback: return a basic data description
        row_count = len(data)
        columns = list(data[0].keys()) if data else []
        summary = (
            f"Query returned {row_count} row(s) with columns: "
            f"{', '.join(columns)}. "
            f"(Automatic summarization is temporarily unavailable.)"
        )

    return summary
