"""
Intent Classifier v2 — Qwen 3.5 enhanced classification with Episodic Memory.

Determines the processing pipeline for a user question, now with:
  • Episodic Memory — records past reasoning chains, avoids repeating
    mistakes, and learns from successful patterns.
  • Task Decomposition — breaks complex questions into sub-tasks
    (LangGraph-inspired agentic orchestration).
  • Confidence Calibration — uses episodic history to refine confidence
    scores based on past accuracy for similar query types.

Intents:
  • text_processing — No database interaction (write, summarize, extract).
  • data_query — Factual question needing SQL → execute → summarize.
  • multi_step_analysis — Complex chain-of-thought reasoning, cross-table
    joins, what-if projections, multi-step logic.

Episodic Memory tiers (LangGraph-inspired):
  • Short-term — current request context and sub-task state.
  • Long-term — persistent domain knowledge (schema facts, business rules).
  • Episodic — past reasoning chains: what worked, what failed, corrections.
"""

from __future__ import annotations

import re
import time
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    TEXT_PROCESSING = "text_processing"
    DATA_QUERY = "data_query"
    MULTI_STEP = "multi_step_analysis"


@dataclass(frozen=True)
class ClassifiedIntent:
    intent: Intent
    confidence: float          # 0.0–1.0
    reasoning: str             # Short explanation for logging
    sub_tasks: list[str] = field(default_factory=list)  # Decomposed sub-tasks for multi-step
    suggested_tables: list[str] = field(default_factory=list)  # Predicted table references
    complexity_score: float = 0.0  # 0.0–1.0, higher = more complex


@dataclass
class EpisodicEntry:
    """A single reasoning episode — records what happened for learning."""
    id: str
    timestamp: float
    user_question: str
    classified_intent: str
    confidence: float
    sub_tasks: list[str]
    sql_generated: str | None = None
    execution_success: bool = True
    error_type: str | None = None
    correction_applied: str | None = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "question_preview": self.user_question[:100],
            "intent": self.classified_intent,
            "confidence": self.confidence,
            "sub_tasks": self.sub_tasks,
            "success": self.execution_success,
            "error_type": self.error_type,
            "correction": self.correction_applied,
        }


class EpisodicMemory:
    """
    In-memory episodic store — records past reasoning chains per user.

    Used to:
      1. Avoid repeating SQL patterns that previously failed.
      2. Boost confidence for query types that consistently succeed.
      3. Auto-suggest sub-task decomposition based on past successes.
    """

    def __init__(self, max_episodes: int = 50):
        self._store: dict[str, list[EpisodicEntry]] = {}  # user_id → episodes
        self._max_episodes = max_episodes

    def record(self, user_id: str, episode: EpisodicEntry) -> None:
        """Record a completed reasoning episode."""
        if user_id not in self._store:
            self._store[user_id] = []
        episodes = self._store[user_id]
        episodes.append(episode)
        # Trim to max size (keep most recent)
        if len(episodes) > self._max_episodes:
            self._store[user_id] = episodes[-self._max_episodes:]

    def get_similar_episodes(
        self,
        user_id: str,
        question: str,
        limit: int = 5,
    ) -> list[EpisodicEntry]:
        """
        Find past episodes similar to the current question.

        Uses keyword overlap for fast matching; this runs on every
        request so it must be lightweight.
        """
        episodes = self._store.get(user_id, [])
        if not episodes:
            return []

        question_words = set(re.findall(r"\b\w{3,}\b", question.lower()))
        if not question_words:
            return []

        scored: list[tuple[float, EpisodicEntry]] = []
        for ep in episodes:
            ep_words = set(re.findall(r"\b\w{3,}\b", ep.user_question.lower()))
            if not ep_words:
                continue
            overlap = len(question_words & ep_words)
            score = overlap / max(len(question_words), len(ep_words))
            if score > 0.2:
                scored.append((score, ep))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:limit]]

    def get_failure_patterns(self, user_id: str) -> list[str]:
        """Get error types from recent failures to avoid repeating them."""
        episodes = self._store.get(user_id, [])
        failures = [
            ep.error_type
            for ep in episodes[-20:]
            if not ep.execution_success and ep.error_type
        ]
        return list(set(failures))

    def get_success_rate(self, user_id: str, intent: str) -> float:
        """Calculate success rate for a specific intent type."""
        episodes = self._store.get(user_id, [])
        matching = [ep for ep in episodes if ep.classified_intent == intent]
        if not matching:
            return 0.5  # No data — neutral
        successes = sum(1 for ep in matching if ep.execution_success)
        return successes / len(matching)

    def get_stats(self, user_id: str) -> dict[str, Any]:
        """Return summary statistics for a user's episodic memory."""
        episodes = self._store.get(user_id, [])
        if not episodes:
            return {"total_episodes": 0}

        intent_counts: dict[str, int] = {}
        for ep in episodes:
            intent_counts[ep.classified_intent] = intent_counts.get(ep.classified_intent, 0) + 1

        return {
            "total_episodes": len(episodes),
            "intent_distribution": intent_counts,
            "overall_success_rate": sum(1 for e in episodes if e.execution_success) / len(episodes),
            "avg_confidence": sum(e.confidence for e in episodes) / len(episodes),
        }


# ── Singleton episodic memory ────────────────────────────
episodic_memory = EpisodicMemory()


# ── Pattern banks ────────────────────────────────────────────────────

# Strong text-processing indicators
_TEXT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(hi|hello|hey|howdy|greetings|good\s*(morning|afternoon|evening)|yo|sup)\s*[!.?]*\s*$", re.I),
     "Greeting / casual conversation"),
    (re.compile(r"\bhow\s+are\s+you\b|\bwhat'?s\s+up\b|\bhow'?s\s+it\s+going\b|\bhow\s+do\s+you\s+do\b", re.I),
     "Greeting / casual conversation"),
    (re.compile(r"^\s*(thanks?|thank\s+you|thx|cheers|bye|goodbye|see\s+you|good\s*bye)\s*[!.?]*\s*$", re.I),
     "Gratitude / farewell"),
    (re.compile(r"^\s*(who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|help\s+me|help)\s*[!.?]*\s*$", re.I),
     "Identity / capability question"),
    (re.compile(r"\b(write|draft|compose|rephrase|rewrite|polish|refine)\b.*\b(email|letter|message|memo|note|report)\b", re.I),
     "Request to write/refine a document"),
    (re.compile(r"\b(summarize|summarise|summary|tldr|tl;dr)\b", re.I),
     "Summarization request"),
    (re.compile(r"\b(extract|pull out)\b.*\b(action items|key points|deliverables|deadlines|takeaways)\b", re.I),
     "Action item / key point extraction"),
    (re.compile(r"\b(meeting notes?|minutes)\b.*\b(extract|clean|organize|format)\b", re.I),
     "Meeting note processing"),
    (re.compile(r"\b(translate|translation)\b", re.I),
     "Translation request"),
    (re.compile(r"\b(brainstorm|suggest ideas|come up with)\b", re.I),
     "Creative / brainstorm task"),
    (re.compile(r"\b(fix|correct|improve)\b.*\b(grammar|tone|spelling|wording|language)\b", re.I),
     "Grammar / tone correction"),
    (re.compile(r"\b(explain|what does|what is|define)\b.*\b(mean|term|concept|acronym)\b", re.I),
     "Definition / explanation request"),
    (re.compile(r"\bhow (do|can|should) (I|we|one)\b", re.I),
     "How-to / advice question"),
]

# Strong data-query indicators (now with table suggestions)
_DATA_PATTERNS: list[tuple[re.Pattern, str, list[str]]] = [
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(sales|revenue)\b", re.I),
     "Direct sales/revenue data retrieval", ["sales", "revenue"]),
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(employees?|staff|workers?)\b", re.I),
     "Employee data retrieval", ["employees"]),
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(attendance|leaves?)\b", re.I),
     "Attendance/leave data retrieval", ["attendance", "leaves"]),
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(projects?|tasks?)\b", re.I),
     "Project data retrieval", ["projects", "tasks"]),
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(customers?|clients?)\b", re.I),
     "Customer data retrieval", ["customers"]),
    (re.compile(r"\b(show|display|list|give me|get|fetch|pull)\b.*\b(expenses?|budget|salary)\b", re.I),
     "Financial data retrieval", ["expenses", "budget"]),
    (re.compile(r"\b(how many|how much|count|total|sum|average|avg|min|max|top|bottom|rank)\b", re.I),
     "Aggregation / statistical question", []),
    (re.compile(r"\b(last|past|previous|this|current|next)\s+(week|month|quarter|year|[0-9]+\s+(days?|months?|years?))\b", re.I),
     "Time-bounded data request", []),
    (re.compile(r"\b(report|breakdown|overview)\b.*\b(by|per|for each|grouped)\b", re.I),
     "Reporting / grouped data request", []),
    (re.compile(r"\b(who|which)\s+(employee|person|rep|staff|manager|team)\b", re.I),
     "People query", ["employees"]),
    (re.compile(r"\b(department|region|branch)\b", re.I),
     "Reference to database entity", ["departments"]),
]

# Multi-step / complex analysis indicators — expanded for Qwen 3.5 agentic workflows
_MULTI_STEP_PATTERNS: list[tuple[re.Pattern, str, list[str]]] = [
    (re.compile(r"\bif\b.*\b(drop|increase|decrease|reduce|grow|rise|fall)\b.*\b(what|how)\b", re.I),
     "What-if / projection scenario", []),
    (re.compile(r"\b(project(ed)?|forecast|predict|estimate)\b.*\b(cash ?flow|revenue|profit|costs?|expenses?)\b", re.I),
     "Financial projection request", ["revenue", "expenses"]),
    (re.compile(r"\b(which department|who contribut)\b.*\b(most|least|biggest|highest|lowest)\b", re.I),
     "Cross-departmental comparison", ["departments", "employees"]),
    (re.compile(r"\b(cover|replace|backup|substitute)\b.*\b(tasks?|work|responsibilities|duties)\b", re.I),
     "Coverage / skill-matching analysis", ["employees", "tasks"]),
    (re.compile(r"\b(approve|check|verify|assess)\b.*\b(leave|vacation|time.?off)\b.*\b(project|task|deadline|critical)\b", re.I),
     "Leave-impact analysis", ["leaves", "projects"]),
    (re.compile(r"\b(growth|trend|year.?over.?year|yoy|month.?over.?month|mom|compare.*to.*same)\b", re.I),
     "Trend / comparison analysis", []),
    (re.compile(r"\b(runway|burn rate|cash reserve|financial health)\b", re.I),
     "Financial health / runway calculation", ["revenue", "expenses"]),
    (re.compile(r"\b(correlat|impact|relationship between|affect)\b", re.I),
     "Correlation / impact analysis", []),
    (re.compile(r"\bstep\s*(?:by|-)?\s*step\b|\bchain of thought\b|\bbreak\s*(?:it\s*)?down\b", re.I),
     "Explicit multi-step / chain-of-thought request", []),
    # ── New patterns for Qwen 3.5 agentic workflows ──────
    (re.compile(r"\b(analyze|analyse)\s+.*\b(and|then)\b\s+.*\b(recommend|suggest|advise)\b", re.I),
     "Analysis → Recommendation pipeline", []),
    (re.compile(r"\b(compare|benchmark)\b.*\b(across|between|among)\b.*\b(department|team|region|quarter)\b", re.I),
     "Cross-entity benchmarking", ["departments"]),
    (re.compile(r"\b(identify|find|detect)\b.*\b(anomal|outlier|unusual|abnormal|spike|dip)\b", re.I),
     "Anomaly detection request", []),
    (re.compile(r"\b(root cause|why did|explain the|reason for)\b.*\b(drop|increase|change|decline|surge)\b", re.I),
     "Root cause analysis", []),
    (re.compile(r"\b(scenario|simulation|model)\b.*\b(what if|assuming|suppose|given that)\b", re.I),
     "Scenario simulation request", []),
    (re.compile(r"\b(prioritize|rank|score)\b.*\b(risk|opportunity|initiative|project)\b", re.I),
     "Prioritization / scoring request", ["projects"]),
    (re.compile(r"\b(plan|roadmap|strategy)\b.*\b(next quarter|next year|upcoming|future)\b", re.I),
     "Strategic planning request", []),
    # ── Admin / cross-module overview patterns ───────────
    (re.compile(r"\b(top|critical|urgent|important)\b.*\b(things?|issues?|concerns?|items?|problems?|priorities)\b", re.I),
     "Cross-module critical issues overview",
     ["orders_timeaction", "finance_invoice", "inventory_inventory", "production_productiontarget", "orders_order"]),
    (re.compile(r"\b(what|anything)\b.*\b(needs?|requires?|demands?)\b.*\b(attention|action|review)\b", re.I),
     "Admin attention / action-required overview",
     ["orders_timeaction", "finance_invoice", "inventory_inventory", "production_production", "inventory_grn"]),
    (re.compile(r"\b(dashboard|overview|status|health)\b.*\b(summary|report|check|overall)\b", re.I),
     "Dashboard-style cross-module overview",
     ["orders_order", "finance_invoice", "inventory_inventory", "production_production", "hr_attendance"]),
    (re.compile(r"\b(overdue|delayed|late|stuck|pending|behind)\b.*\b(across|all|everything|company|modules?)\b", re.I),
     "Cross-module overdue/delayed items scan",
     ["orders_timeaction", "finance_invoice", "inventory_grn", "production_production"]),
    (re.compile(r"\b(alerts?|warnings?|red flags?)\b", re.I),
     "Smart alert / warning query",
     ["orders_timeaction", "finance_invoice", "inventory_inventory", "production_productiontarget", "inventory_grn"]),
    (re.compile(r"\bwhat('?s| is)\s+(going on|happening|the situation)\b", re.I),
     "General situation overview",
     ["orders_order", "finance_invoice", "inventory_inventory", "production_production"]),
]

# If question contains a large text block (>200 chars) that looks like
# pasted content to process, it's likely text_processing
_LONG_TEXT_THRESHOLD = 200


def _extract_sub_tasks(question: str, intent: Intent) -> list[str]:
    """
    Decompose a complex question into ordered sub-tasks.
    (LangGraph-inspired task decomposition)

    For multi_step_analysis, this identifies the logical stages:
      1. Data gathering  (which tables/metrics to query)
      2. Computation     (aggregation, comparison, projection)
      3. Analysis        (interpretation, trend identification)
      4. Recommendation  (actionable output)
    """
    if intent == Intent.TEXT_PROCESSING:
        return ["Process text request directly"]

    if intent == Intent.DATA_QUERY:
        return ["Generate SQL query", "Execute and validate", "Summarize results"]

    # Multi-step decomposition
    sub_tasks: list[str] = []
    q_lower = question.lower()

    # ── Detect cross-module / admin overview queries ─────
    is_admin_overview = bool(re.search(
        r"\b(top|critical|urgent|important|attention|overview|dashboard|alerts?|"
        r"warnings?|red flags?|what.?s going on|what.?s happening|health check)\b",
        q_lower,
    ))

    if is_admin_overview:
        sub_tasks.append("Check overdue invoices: finance_invoice WHERE status='sent' AND due_date < today")
        sub_tasks.append("Check low stock items: inventory_inventory WHERE quantity <= min_level AND min_level > 0")
        sub_tasks.append("Check overdue T&A milestones: orders_timeaction WHERE status='pending' AND planned_date < today")
        sub_tasks.append("Check production efficiency: production_productiontarget WHERE achieved_qty/target_qty < 0.5")
        sub_tasks.append("Check pending GRN approvals: inventory_grn WHERE status='pending'")
        sub_tasks.append("Check upcoming ship dates: orders_order WHERE ship_date within 7 days")
        sub_tasks.append("Combine all findings using UNION ALL with category labels, ordered by severity")
        return sub_tasks

    # Stage 1: Data gathering
    data_entities = []
    for entity in ["sales", "revenue", "employees", "attendance", "projects",
                    "expenses", "budget", "customers", "departments", "leaves"]:
        if entity in q_lower:
            data_entities.append(entity)
    if data_entities:
        sub_tasks.append(f"Gather data: {', '.join(data_entities)}")
    else:
        sub_tasks.append("Gather relevant data from database")

    # Stage 2: Computation
    if re.search(r"\b(compare|benchmark|rank|vs|versus)\b", q_lower):
        sub_tasks.append("Compute comparison metrics across entities")
    if re.search(r"\b(trend|growth|yoy|mom|over time)\b", q_lower):
        sub_tasks.append("Calculate temporal trends and growth rates")
    if re.search(r"\b(project|forecast|predict|if\b.*\bdrop|if\b.*\bincrease)\b", q_lower):
        sub_tasks.append("Generate projections using SQL arithmetic")
    if re.search(r"\b(correlat|impact|relationship)\b", q_lower):
        sub_tasks.append("Analyze correlations between variables")
    if not any("Compute" in t or "Calculate" in t or "Generate" in t or "Analyze" in t for t in sub_tasks):
        sub_tasks.append("Compute aggregations and derived metrics")

    # Stage 3: Validation checkpoint
    sub_tasks.append("Validate intermediate results before final analysis")

    # Stage 4: Analysis & Recommendations
    if re.search(r"\b(recommend|suggest|advise|action|next steps?)\b", q_lower):
        sub_tasks.append("Generate actionable recommendations")
    elif re.search(r"\b(risk|concern|warning|alert)\b", q_lower):
        sub_tasks.append("Flag risks and concerns from the data")
    else:
        sub_tasks.append("Synthesize findings into executive summary")

    return sub_tasks


def classify(
    question: str,
    user_id: str | None = None,
) -> ClassifiedIntent:
    """
    Classify the user's question into one of the three intents.

    Enhanced with:
      • Episodic memory calibration (if user_id is provided)
      • Sub-task decomposition for multi-step queries
      • Table suggestion for faster schema filtering
      • Complexity scoring

    Uses pattern matching with a scoring system. Falls back to
    data_query (the most common intent) on ambiguity.
    """
    text_score = 0.0
    data_score = 0.0
    multi_score = 0.0

    text_reasons: list[str] = []
    data_reasons: list[str] = []
    multi_reasons: list[str] = []
    suggested_tables: list[str] = []

    # ── Pattern matching ─────────────────────────────────
    for pat, reason in _TEXT_PATTERNS:
        if pat.search(question):
            text_score += 1.0
            text_reasons.append(reason)

    for pat, reason, tables in _DATA_PATTERNS:
        if pat.search(question):
            data_score += 1.0
            data_reasons.append(reason)
            suggested_tables.extend(tables)

    for pat, reason, tables in _MULTI_STEP_PATTERNS:
        if pat.search(question):
            multi_score += 1.0
            multi_reasons.append(reason)
            suggested_tables.extend(tables)

    # ── Heuristics ───────────────────────────────────────

    # Pasted long text → text processing
    lines = question.strip().split("\n")
    if len(question) > _LONG_TEXT_THRESHOLD and len(lines) > 4:
        text_score += 1.5
        text_reasons.append("Long pasted text detected")

    # Quoted text blocks → text processing
    if '"""' in question or "'''" in question or question.count('"') >= 4:
        text_score += 0.8
        text_reasons.append("Quoted text block detected")

    # Multiple "and" connectors + cross-table keywords → multi-step
    and_count = len(re.findall(r"\band\b", question, re.I))
    if and_count >= 2 and (data_score > 0 or multi_score > 0):
        multi_score += 0.5
        multi_reasons.append("Multiple clauses suggest complex analysis")

    # If question mentions "then" or "next" or numbered steps → multi-step
    if re.search(r"\bthen\b|\bafter that\b|\bstep [0-9]\b|\b(first|second|third)\b", question, re.I):
        multi_score += 0.5
        multi_reasons.append("Sequential step language detected")

    # ── Complexity score ─────────────────────────────────
    word_count = len(question.split())
    clause_count = len(re.findall(r"\b(and|or|but|then|also|additionally|furthermore|moreover)\b", question, re.I))
    question_marks = question.count("?")

    complexity = min(1.0, (
        (word_count / 100) * 0.3 +
        (clause_count / 5) * 0.3 +
        (question_marks / 3) * 0.1 +
        (multi_score / 4) * 0.3
    ))

    # ── Episodic memory calibration ──────────────────────
    episodic_boost = 0.0
    if user_id:
        similar = episodic_memory.get_similar_episodes(user_id, question)
        if similar:
            # If similar past queries succeeded as multi-step, boost multi_score
            for ep in similar:
                if ep.classified_intent == Intent.MULTI_STEP.value and ep.execution_success:
                    episodic_boost += 0.3
                    multi_reasons.append("Episodic: similar query succeeded as multi-step")
                elif ep.classified_intent == Intent.DATA_QUERY.value and ep.execution_success:
                    data_score += 0.2
                    data_reasons.append("Episodic: similar query succeeded as data_query")

            # If similar queries failed, adjust confidence down
            failures = [ep for ep in similar if not ep.execution_success]
            if failures:
                for f in failures:
                    if f.classified_intent == Intent.DATA_QUERY.value:
                        multi_score += 0.3  # Try multi-step instead
                        multi_reasons.append("Episodic: similar data_query failed before — escalating")

        multi_score += episodic_boost

    # ── Decision ─────────────────────────────────────────
    total = text_score + data_score + multi_score

    # Deduplicate suggested tables
    suggested_tables = list(dict.fromkeys(suggested_tables))

    if total == 0:
        sub_tasks = _extract_sub_tasks(question, Intent.DATA_QUERY)
        return ClassifiedIntent(
            intent=Intent.DATA_QUERY,
            confidence=0.4,
            reasoning="No strong patterns detected; defaulting to data query",
            sub_tasks=sub_tasks,
            suggested_tables=suggested_tables,
            complexity_score=complexity,
        )

    # Multi-step wins if it has any signal AND data_score also fires
    if multi_score > 0 and multi_score >= text_score:
        intent = Intent.MULTI_STEP
        confidence = min(multi_score / (total + 0.5), 1.0)
        reasoning = "; ".join(multi_reasons)
    elif text_score > data_score and text_score > multi_score:
        intent = Intent.TEXT_PROCESSING
        confidence = min(text_score / (total + 0.5), 1.0)
        reasoning = "; ".join(text_reasons)
    else:
        intent = Intent.DATA_QUERY
        confidence = min(data_score / (total + 0.5), 1.0)
        reasoning = "; ".join(data_reasons) if data_reasons else "Default to data query"

    # Apply episodic calibration to confidence
    if user_id:
        success_rate = episodic_memory.get_success_rate(user_id, intent.value)
        confidence = confidence * 0.7 + success_rate * 0.3

    sub_tasks = _extract_sub_tasks(question, intent)

    return ClassifiedIntent(
        intent=intent,
        confidence=round(confidence, 3),
        reasoning=reasoning,
        sub_tasks=sub_tasks,
        suggested_tables=suggested_tables,
        complexity_score=round(complexity, 3),
    )
