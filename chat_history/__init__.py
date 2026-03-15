"""
Chat History & Long-Term Memory — SQLite-backed storage.

Provides per-user conversation sessions (like ChatGPT/Gemini) and
RAG long-term memory that remembers user preferences & important
facts across sessions.

Tables:
  chat_sessions  — session metadata (id, user_id, title, timestamps)
  chat_messages  — ordered messages within a session
  user_memories  — extracted facts / preferences per user (long-term)
"""

from __future__ import annotations

import uuid
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

import aiosqlite

# ── Defaults ─────────────────────────────────────────────

_DB_PATH = "chat_history.db"
_MAX_CONTEXT_MESSAGES = 20          # last N messages sent as context
_MAX_MEMORIES_IN_PROMPT = 10        # top-K memories injected per request
_MEMORY_RELEVANCE_THRESHOLD = 0.3   # minimum keyword overlap ratio


# ── Data Classes ─────────────────────────────────────────

@dataclass
class ChatSession:
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


@dataclass
class ChatMessage:
    id: str
    session_id: str
    role: str           # "user" | "assistant" | "system"
    content: str
    sql: str | None = None
    data_summary: str | None = None
    intent: str | None = None
    created_at: str = ""


@dataclass
class UserMemory:
    id: str
    user_id: str
    category: str       # "preference" | "fact" | "context" | "instruction"
    content: str
    source: str          # "auto" | "manual"
    importance: float    # 0.0–1.0, higher = more important
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0


# ── Database Initialization ──────────────────────────────

async def _get_db(db_path: str = _DB_PATH) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db(db_path: str = _DB_PATH) -> None:
    """Create tables if they don't exist."""
    db = await _get_db(db_path)
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT 'New Chat',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON chat_sessions(user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS chat_messages (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content     TEXT NOT NULL,
                sql_query   TEXT,
                data_summary TEXT,
                intent      TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON chat_messages(session_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS user_memories (
                id            TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL,
                category      TEXT NOT NULL DEFAULT 'fact',
                content       TEXT NOT NULL,
                source        TEXT NOT NULL DEFAULT 'auto',
                importance    REAL NOT NULL DEFAULT 0.5,
                keywords      TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user
                ON user_memories(user_id, importance DESC);

            CREATE TABLE IF NOT EXISTS episodic_memories (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                question        TEXT NOT NULL,
                classified_intent TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                sub_tasks       TEXT NOT NULL DEFAULT '[]',
                sql_generated   TEXT,
                execution_success INTEGER NOT NULL DEFAULT 1,
                error_type      TEXT,
                correction      TEXT,
                execution_time_ms REAL NOT NULL DEFAULT 0.0,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episodic_user
                ON episodic_memories(user_id, created_at DESC);
        """)
        await db.commit()
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════
#  CHAT SESSION OPERATIONS
# ══════════════════════════════════════════════════════════

async def create_session(
    user_id: str,
    title: str = "New Chat",
    db_path: str = _DB_PATH,
) -> ChatSession:
    """Create a new chat session for a user."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = await _get_db(db_path)
    try:
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, title, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return ChatSession(
        id=session_id, user_id=user_id, title=title,
        created_at=now, updated_at=now, message_count=0,
    )


async def list_sessions(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
    db_path: str = _DB_PATH,
) -> list[ChatSession]:
    """List all sessions for a user, newest first."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.id
            WHERE s.user_id = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            ChatSession(
                id=r["id"], user_id=r["user_id"], title=r["title"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                message_count=r["message_count"],
            )
            for r in rows
        ]
    finally:
        await db.close()


async def get_session(
    session_id: str,
    user_id: str,
    db_path: str = _DB_PATH,
) -> ChatSession | None:
    """Get a specific session (enforces user ownership)."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.id
            WHERE s.id = ? AND s.user_id = ?
            GROUP BY s.id
            """,
            (session_id, user_id),
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return ChatSession(
            id=r["id"], user_id=r["user_id"], title=r["title"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            message_count=r["message_count"],
        )
    finally:
        await db.close()


async def update_session_title(
    session_id: str,
    user_id: str,
    title: str,
    db_path: str = _DB_PATH,
) -> bool:
    """Update a session's title. Returns True if updated."""
    now = datetime.now(timezone.utc).isoformat()
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (title, now, session_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_session(
    session_id: str,
    user_id: str,
    db_path: str = _DB_PATH,
) -> bool:
    """Delete a session and all its messages. Returns True if deleted."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════
#  MESSAGE OPERATIONS
# ══════════════════════════════════════════════════════════

async def add_message(
    session_id: str,
    role: str,
    content: str,
    sql: str | None = None,
    data_summary: str | None = None,
    intent: str | None = None,
    db_path: str = _DB_PATH,
) -> ChatMessage:
    """Add a message to a session."""
    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = await _get_db(db_path)
    try:
        await db.execute(
            "INSERT INTO chat_messages "
            "(id, session_id, role, content, sql_query, data_summary, intent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, sql, data_summary, intent, now),
        )
        # Touch the session's updated_at
        await db.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        await db.commit()
    finally:
        await db.close()
    return ChatMessage(
        id=msg_id, session_id=session_id, role=role, content=content,
        sql=sql, data_summary=data_summary, intent=intent, created_at=now,
    )


async def get_messages(
    session_id: str,
    limit: int = 100,
    db_path: str = _DB_PATH,
) -> list[ChatMessage]:
    """Get all messages in a session, oldest first."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            ChatMessage(
                id=r["id"], session_id=r["session_id"], role=r["role"],
                content=r["content"], sql=r["sql_query"],
                data_summary=r["data_summary"], intent=r["intent"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        await db.close()


async def get_context_messages(
    session_id: str,
    max_messages: int = _MAX_CONTEXT_MESSAGES,
    db_path: str = _DB_PATH,
) -> list[dict[str, str]]:
    """
    Get the last N messages formatted as OpenAI-style messages
    for injecting into the LLM prompt as conversation context.
    """
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            """
            SELECT role, content FROM chat_messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY created_at DESC LIMIT ?
            """,
            (session_id, max_messages),
        )
        rows = await cursor.fetchall()
        # Reverse to chronological order
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════
#  LONG-TERM MEMORY (RAG)
# ══════════════════════════════════════════════════════════

def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text for matching."""
    import re
    # Lowercase, split on non-alphanumeric, filter short/stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "must", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "into", "through",
        "during", "before", "after", "above", "below", "between", "out",
        "up", "down", "about", "this", "that", "these", "those", "it",
        "its", "not", "no", "nor", "and", "but", "or", "so", "if", "then",
        "than", "too", "very", "just", "also", "all", "each", "every",
        "both", "few", "more", "most", "own", "same", "other", "such",
        "some", "any", "only", "how", "what", "when", "where", "which",
        "who", "whom", "why", "i", "me", "my", "we", "our", "you", "your",
        "he", "him", "his", "she", "her", "they", "them", "their",
        "show", "give", "get", "tell", "please", "want", "need", "like",
    }
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - stop_words


def _keyword_similarity(keywords_a: set[str], keywords_b: set[str]) -> float:
    """Jaccard-like similarity between two keyword sets."""
    if not keywords_a or not keywords_b:
        return 0.0
    intersection = keywords_a & keywords_b
    union = keywords_a | keywords_b
    return len(intersection) / len(union)


async def store_memory(
    user_id: str,
    content: str,
    category: str = "fact",
    source: str = "auto",
    importance: float = 0.5,
    db_path: str = _DB_PATH,
) -> UserMemory:
    """Store a long-term memory for a user."""
    mem_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    keywords = ",".join(sorted(_extract_keywords(content)))
    db = await _get_db(db_path)
    try:
        # Avoid exact duplicates
        cursor = await db.execute(
            "SELECT id FROM user_memories WHERE user_id = ? AND content = ?",
            (user_id, content),
        )
        existing = await cursor.fetchone()
        if existing:
            # Just bump importance
            await db.execute(
                "UPDATE user_memories SET importance = MIN(importance + 0.1, 1.0), "
                "last_accessed = ? WHERE id = ?",
                (now, existing["id"]),
            )
            await db.commit()
            return UserMemory(
                id=existing["id"], user_id=user_id, category=category,
                content=content, source=source, importance=importance,
                created_at=now, last_accessed=now,
            )

        await db.execute(
            "INSERT INTO user_memories "
            "(id, user_id, category, content, source, importance, keywords, created_at, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mem_id, user_id, category, content, source, importance, keywords, now, now),
        )
        await db.commit()
    finally:
        await db.close()
    return UserMemory(
        id=mem_id, user_id=user_id, category=category,
        content=content, source=source, importance=importance,
        created_at=now, last_accessed=now,
    )


async def retrieve_memories(
    user_id: str,
    query: str,
    top_k: int = _MAX_MEMORIES_IN_PROMPT,
    db_path: str = _DB_PATH,
) -> list[UserMemory]:
    """
    Retrieve relevant memories for a user based on keyword similarity.
    Uses keyword overlap scoring + importance weighting.
    """
    query_keywords = _extract_keywords(query)
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT * FROM user_memories WHERE user_id = ? ORDER BY importance DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()

        scored = []
        for r in rows:
            mem_keywords = set(r["keywords"].split(",")) if r["keywords"] else set()
            similarity = _keyword_similarity(query_keywords, mem_keywords)
            # Combined score: 70% relevance + 30% importance
            score = 0.7 * similarity + 0.3 * r["importance"]
            if similarity >= _MEMORY_RELEVANCE_THRESHOLD or r["importance"] >= 0.8:
                scored.append((score, r))

        # Sort by combined score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        now = datetime.now(timezone.utc).isoformat()
        results = []
        for _, r in scored[:top_k]:
            # Update access stats
            await db.execute(
                "UPDATE user_memories SET last_accessed = ?, access_count = access_count + 1 "
                "WHERE id = ?",
                (now, r["id"]),
            )
            results.append(UserMemory(
                id=r["id"], user_id=r["user_id"], category=r["category"],
                content=r["content"], source=r["source"],
                importance=r["importance"], created_at=r["created_at"],
                last_accessed=now, access_count=r["access_count"] + 1,
            ))
        if results:
            await db.commit()
        return results
    finally:
        await db.close()


async def list_memories(
    user_id: str,
    db_path: str = _DB_PATH,
) -> list[UserMemory]:
    """List all memories for a user, by importance."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "SELECT * FROM user_memories WHERE user_id = ? "
            "ORDER BY importance DESC, created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            UserMemory(
                id=r["id"], user_id=r["user_id"], category=r["category"],
                content=r["content"], source=r["source"],
                importance=r["importance"], created_at=r["created_at"],
                last_accessed=r["last_accessed"], access_count=r["access_count"],
            )
            for r in rows
        ]
    finally:
        await db.close()


async def delete_memory(
    memory_id: str,
    user_id: str,
    db_path: str = _DB_PATH,
) -> bool:
    """Delete a specific memory. Returns True if deleted."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "DELETE FROM user_memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def clear_memories(
    user_id: str,
    db_path: str = _DB_PATH,
) -> int:
    """Clear ALL memories for a user. Returns count deleted."""
    db = await _get_db(db_path)
    try:
        cursor = await db.execute(
            "DELETE FROM user_memories WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════
#  MEMORY EXTRACTION (Auto-learn from conversations)
# ══════════════════════════════════════════════════════════

# Patterns that indicate extractable information
_MEMORY_PATTERNS: list[tuple[str, str, float]] = [
    # (pattern_description, category, importance)
    ("prefers|prefer|preference|favorite|favourite|always want|like to see", "preference", 0.7),
    ("remember that|note that|keep in mind|important:|fyi:", "instruction", 0.8),
    ("my name is|i am|i work in|my department|my role|i manage|i lead", "context", 0.6),
    ("format as|show as|display as|i like the format|in table format|bullet points", "preference", 0.7),
    ("daily|weekly|monthly|every monday|every morning|routine|regularly", "preference", 0.6),
    ("don't show|never include|skip|hide|exclude|i don't need", "preference", 0.7),
    ("focus on|prioritize|most important|key metric|kpi|main concern", "preference", 0.8),
]


async def extract_memories_from_exchange(
    user_id: str,
    user_message: str,
    assistant_response: str,
    db_path: str = _DB_PATH,
) -> list[UserMemory]:
    """
    Auto-extract memories from a user-assistant exchange.
    Looks for preference indicators, personal info, and recurring patterns.
    """
    import re
    extracted = []

    # Check user message against patterns
    msg_lower = user_message.lower()
    for pattern_str, category, importance in _MEMORY_PATTERNS:
        if re.search(pattern_str, msg_lower):
            # Store the user's original message as memory content
            # Truncate long messages
            content = user_message[:300].strip()
            if len(user_message) > 300:
                content += "..."
            mem = await store_memory(
                user_id=user_id,
                content=content,
                category=category,
                source="auto",
                importance=importance,
                db_path=db_path,
            )
            extracted.append(mem)
            break  # One extraction per message to avoid spam

    return extracted


async def store_episodic_memory(
    user_id: str,
    question: str,
    classified_intent: str,
    confidence: float = 0.5,
    sub_tasks: list[str] | None = None,
    sql_generated: str | None = None,
    execution_success: bool = True,
    error_type: str | None = None,
    correction: str | None = None,
    execution_time_ms: float = 0.0,
    db_path: str = _DB_PATH,
) -> dict:
    """Store an episodic (reasoning chain) memory from a chat interaction."""
    import json as _json
    ep_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sub_tasks_json = _json.dumps(sub_tasks or [])
    db = await _get_db(db_path)
    try:
        await db.execute(
            "INSERT INTO episodic_memories "
            "(id, user_id, question, classified_intent, confidence, sub_tasks, "
            "sql_generated, execution_success, error_type, correction, "
            "execution_time_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ep_id, user_id, question[:500], classified_intent, confidence,
             sub_tasks_json, sql_generated, int(execution_success),
             error_type, correction, execution_time_ms, now),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": ep_id, "user_id": user_id, "question": question[:500],
            "classified_intent": classified_intent, "created_at": now}


def format_memories_for_prompt(memories: list[UserMemory]) -> str:
    """Format retrieved memories as a context block for the LLM prompt."""
    if not memories:
        return ""

    lines = ["[User Context — Long-Term Memory]"]
    for mem in memories:
        prefix = {
            "preference": "Preference",
            "fact": "Known Fact",
            "context": "User Info",
            "instruction": "User Instruction",
        }.get(mem.category, "Note")
        lines.append(f"• {prefix}: {mem.content}")

    lines.append("[End of User Context]\n")
    return "\n".join(lines)


# ── Auto-title generation ────────────────────────────────

def generate_session_title(first_message: str) -> str:
    """Generate a short title from the first user message."""
    # Clean and truncate
    title = first_message.strip()
    # Remove common prefixes
    for prefix in ["show me ", "what is ", "give me ", "can you ", "please ", "i want "]:
        if title.lower().startswith(prefix):
            title = title[len(prefix):]
            break
    # Capitalize and truncate
    title = title[:60].strip()
    if len(first_message) > 60:
        title += "..."
    return title.capitalize() if title else "New Chat"
