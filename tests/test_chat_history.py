"""
Tests for Chat History & Long-Term Memory module.

Covers:
  - Database initialization
  - Session CRUD (create, list, get, rename, delete)
  - Message storage and retrieval
  - Context message formatting
  - Memory storage (with dedup)
  - Memory retrieval (keyword RAG)
  - Memory listing, deletion, clearing
  - Keyword extraction and similarity
  - Memory extraction from exchanges
  - Session title generation
  - Prompt formatting
"""

import os
import asyncio
import pytest

from chat_history import (
    # DB init
    init_db,
    # Sessions
    create_session,
    list_sessions,
    get_session,
    update_session_title,
    delete_session,
    # Messages
    add_message,
    get_messages,
    get_context_messages,
    # Memory
    store_memory,
    retrieve_memories,
    list_memories,
    delete_memory,
    clear_memories,
    # Memory extraction
    extract_memories_from_exchange,
    # Utilities
    format_memories_for_prompt,
    generate_session_title,
    _extract_keywords,
    _keyword_similarity,
    # Data classes
    ChatSession,
    ChatMessage,
    UserMemory,
)


# ── Fixtures ─────────────────────────────────────────────

TEST_DB = "test_chat_history.db"
USER_A = "user-alice-001"
USER_B = "user-bob-002"


@pytest.fixture(autouse=True)
async def fresh_db():
    """Create a fresh test database before each test, remove after."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ══════════════════════════════════════════════════════════
#  DATABASE INIT
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_init_db_creates_file():
    """init_db should create the SQLite database file."""
    assert os.path.exists(TEST_DB)


@pytest.mark.asyncio
async def test_init_db_idempotent():
    """Calling init_db twice should not raise."""
    await init_db(TEST_DB)
    assert os.path.exists(TEST_DB)


# ══════════════════════════════════════════════════════════
#  SESSION OPERATIONS
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_session_returns_session():
    s = await create_session(USER_A, title="Test Chat", db_path=TEST_DB)
    assert isinstance(s, ChatSession)
    assert s.user_id == USER_A
    assert s.title == "Test Chat"
    assert s.message_count == 0
    assert s.id  # non-empty UUID


@pytest.mark.asyncio
async def test_create_session_default_title():
    s = await create_session(USER_A, db_path=TEST_DB)
    assert s.title == "New Chat"


@pytest.mark.asyncio
async def test_list_sessions_empty():
    sessions = await list_sessions(USER_A, db_path=TEST_DB)
    assert sessions == []


@pytest.mark.asyncio
async def test_list_sessions_returns_created():
    await create_session(USER_A, title="Chat 1", db_path=TEST_DB)
    await create_session(USER_A, title="Chat 2", db_path=TEST_DB)
    sessions = await list_sessions(USER_A, db_path=TEST_DB)
    assert len(sessions) == 2
    # Most recent first
    assert sessions[0].title == "Chat 2"
    assert sessions[1].title == "Chat 1"


@pytest.mark.asyncio
async def test_list_sessions_user_isolation():
    """User A should not see User B's sessions."""
    await create_session(USER_A, title="A's Chat", db_path=TEST_DB)
    await create_session(USER_B, title="B's Chat", db_path=TEST_DB)
    sessions_a = await list_sessions(USER_A, db_path=TEST_DB)
    sessions_b = await list_sessions(USER_B, db_path=TEST_DB)
    assert len(sessions_a) == 1
    assert len(sessions_b) == 1
    assert sessions_a[0].title == "A's Chat"


@pytest.mark.asyncio
async def test_list_sessions_with_limit_and_offset():
    for i in range(5):
        await create_session(USER_A, title=f"Chat {i}", db_path=TEST_DB)
    first_two = await list_sessions(USER_A, limit=2, offset=0, db_path=TEST_DB)
    assert len(first_two) == 2
    next_two = await list_sessions(USER_A, limit=2, offset=2, db_path=TEST_DB)
    assert len(next_two) == 2
    last_one = await list_sessions(USER_A, limit=2, offset=4, db_path=TEST_DB)
    assert len(last_one) == 1


@pytest.mark.asyncio
async def test_get_session_found():
    s = await create_session(USER_A, title="My Chat", db_path=TEST_DB)
    found = await get_session(s.id, USER_A, db_path=TEST_DB)
    assert found is not None
    assert found.id == s.id
    assert found.title == "My Chat"


@pytest.mark.asyncio
async def test_get_session_wrong_user():
    """User B should not access User A's session."""
    s = await create_session(USER_A, title="Secret", db_path=TEST_DB)
    found = await get_session(s.id, USER_B, db_path=TEST_DB)
    assert found is None


@pytest.mark.asyncio
async def test_get_session_not_found():
    found = await get_session("nonexistent-id", USER_A, db_path=TEST_DB)
    assert found is None


@pytest.mark.asyncio
async def test_update_session_title():
    s = await create_session(USER_A, title="Old", db_path=TEST_DB)
    ok = await update_session_title(s.id, USER_A, "New Title", db_path=TEST_DB)
    assert ok is True
    updated = await get_session(s.id, USER_A, db_path=TEST_DB)
    assert updated.title == "New Title"


@pytest.mark.asyncio
async def test_update_session_title_wrong_user():
    s = await create_session(USER_A, title="Safe", db_path=TEST_DB)
    ok = await update_session_title(s.id, USER_B, "Hacked!", db_path=TEST_DB)
    assert ok is False
    original = await get_session(s.id, USER_A, db_path=TEST_DB)
    assert original.title == "Safe"


@pytest.mark.asyncio
async def test_delete_session():
    s = await create_session(USER_A, title="Delete Me", db_path=TEST_DB)
    ok = await delete_session(s.id, USER_A, db_path=TEST_DB)
    assert ok is True
    found = await get_session(s.id, USER_A, db_path=TEST_DB)
    assert found is None


@pytest.mark.asyncio
async def test_delete_session_wrong_user():
    s = await create_session(USER_A, title="Protected", db_path=TEST_DB)
    ok = await delete_session(s.id, USER_B, db_path=TEST_DB)
    assert ok is False
    found = await get_session(s.id, USER_A, db_path=TEST_DB)
    assert found is not None


@pytest.mark.asyncio
async def test_delete_session_cascades_messages():
    """Deleting a session should also delete all its messages."""
    s = await create_session(USER_A, title="Chat", db_path=TEST_DB)
    await add_message(s.id, "user", "hello", db_path=TEST_DB)
    await add_message(s.id, "assistant", "hi!", db_path=TEST_DB)
    await delete_session(s.id, USER_A, db_path=TEST_DB)
    msgs = await get_messages(s.id, db_path=TEST_DB)
    assert len(msgs) == 0


# ══════════════════════════════════════════════════════════
#  MESSAGE OPERATIONS
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_add_message_basic():
    s = await create_session(USER_A, db_path=TEST_DB)
    msg = await add_message(s.id, "user", "Hello!", db_path=TEST_DB)
    assert isinstance(msg, ChatMessage)
    assert msg.role == "user"
    assert msg.content == "Hello!"
    assert msg.session_id == s.id


@pytest.mark.asyncio
async def test_add_message_with_metadata():
    s = await create_session(USER_A, db_path=TEST_DB)
    msg = await add_message(
        s.id, "assistant", "Here are the results",
        sql="SELECT * FROM employees",
        data_summary='[{"name":"Alice"}]',
        intent="data_query",
        db_path=TEST_DB,
    )
    assert msg.sql == "SELECT * FROM employees"
    assert msg.data_summary == '[{"name":"Alice"}]'
    assert msg.intent == "data_query"


@pytest.mark.asyncio
async def test_get_messages_ordered():
    s = await create_session(USER_A, db_path=TEST_DB)
    await add_message(s.id, "user", "First", db_path=TEST_DB)
    await add_message(s.id, "assistant", "Second", db_path=TEST_DB)
    await add_message(s.id, "user", "Third", db_path=TEST_DB)
    msgs = await get_messages(s.id, db_path=TEST_DB)
    assert len(msgs) == 3
    assert msgs[0].content == "First"
    assert msgs[1].content == "Second"
    assert msgs[2].content == "Third"


@pytest.mark.asyncio
async def test_get_messages_empty_session():
    s = await create_session(USER_A, db_path=TEST_DB)
    msgs = await get_messages(s.id, db_path=TEST_DB)
    assert msgs == []


@pytest.mark.asyncio
async def test_session_message_count():
    """list_sessions should show correct message_count."""
    s = await create_session(USER_A, db_path=TEST_DB)
    await add_message(s.id, "user", "msg1", db_path=TEST_DB)
    await add_message(s.id, "assistant", "msg2", db_path=TEST_DB)
    sessions = await list_sessions(USER_A, db_path=TEST_DB)
    assert sessions[0].message_count == 2


@pytest.mark.asyncio
async def test_get_context_messages():
    """get_context_messages returns OpenAI-style dicts, in chronological order."""
    s = await create_session(USER_A, db_path=TEST_DB)
    await add_message(s.id, "user", "Q1", db_path=TEST_DB)
    await add_message(s.id, "assistant", "A1", db_path=TEST_DB)
    await add_message(s.id, "user", "Q2", db_path=TEST_DB)
    ctx = await get_context_messages(s.id, max_messages=10, db_path=TEST_DB)
    assert len(ctx) == 3
    assert ctx[0] == {"role": "user", "content": "Q1"}
    assert ctx[1] == {"role": "assistant", "content": "A1"}
    assert ctx[2] == {"role": "user", "content": "Q2"}


@pytest.mark.asyncio
async def test_get_context_messages_limit():
    """Should return only last N messages."""
    s = await create_session(USER_A, db_path=TEST_DB)
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        await add_message(s.id, role, f"msg{i}", db_path=TEST_DB)
    ctx = await get_context_messages(s.id, max_messages=4, db_path=TEST_DB)
    assert len(ctx) == 4
    # Should be the last 4, in chronological order
    assert ctx[0]["content"] == "msg6"
    assert ctx[3]["content"] == "msg9"


@pytest.mark.asyncio
async def test_get_context_excludes_system():
    """Context messages should exclude system messages."""
    s = await create_session(USER_A, db_path=TEST_DB)
    await add_message(s.id, "system", "System prompt", db_path=TEST_DB)
    await add_message(s.id, "user", "Q1", db_path=TEST_DB)
    await add_message(s.id, "assistant", "A1", db_path=TEST_DB)
    ctx = await get_context_messages(s.id, db_path=TEST_DB)
    assert len(ctx) == 2
    assert all(m["role"] != "system" for m in ctx)


# ══════════════════════════════════════════════════════════
#  MEMORY OPERATIONS
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_store_memory_basic():
    m = await store_memory(USER_A, content="Prefers table format", category="preference", db_path=TEST_DB)
    assert isinstance(m, UserMemory)
    assert m.user_id == USER_A
    assert m.content == "Prefers table format"
    assert m.category == "preference"
    assert m.source == "auto"


@pytest.mark.asyncio
async def test_store_memory_manual():
    m = await store_memory(USER_A, content="My timezone is EST", source="manual", importance=0.8, db_path=TEST_DB)
    assert m.source == "manual"
    assert m.importance == 0.8


@pytest.mark.asyncio
async def test_store_memory_dedup():
    """Storing the same content twice should not create a duplicate."""
    m1 = await store_memory(USER_A, content="Same content", db_path=TEST_DB)
    m2 = await store_memory(USER_A, content="Same content", db_path=TEST_DB)
    all_mems = await list_memories(USER_A, db_path=TEST_DB)
    assert len(all_mems) == 1


@pytest.mark.asyncio
async def test_list_memories_empty():
    mems = await list_memories(USER_A, db_path=TEST_DB)
    assert mems == []


@pytest.mark.asyncio
async def test_list_memories_ordered_by_importance():
    await store_memory(USER_A, content="Low importance", importance=0.3, db_path=TEST_DB)
    await store_memory(USER_A, content="High importance", importance=0.9, db_path=TEST_DB)
    await store_memory(USER_A, content="Medium importance", importance=0.6, db_path=TEST_DB)
    mems = await list_memories(USER_A, db_path=TEST_DB)
    assert mems[0].content == "High importance"
    assert mems[1].content == "Medium importance"
    assert mems[2].content == "Low importance"


@pytest.mark.asyncio
async def test_list_memories_user_isolation():
    await store_memory(USER_A, content="Alice's memory", db_path=TEST_DB)
    await store_memory(USER_B, content="Bob's memory", db_path=TEST_DB)
    a_mems = await list_memories(USER_A, db_path=TEST_DB)
    b_mems = await list_memories(USER_B, db_path=TEST_DB)
    assert len(a_mems) == 1
    assert len(b_mems) == 1
    assert a_mems[0].content == "Alice's memory"


@pytest.mark.asyncio
async def test_delete_memory():
    m = await store_memory(USER_A, content="To delete", db_path=TEST_DB)
    ok = await delete_memory(m.id, USER_A, db_path=TEST_DB)
    assert ok is True
    mems = await list_memories(USER_A, db_path=TEST_DB)
    assert len(mems) == 0


@pytest.mark.asyncio
async def test_delete_memory_wrong_user():
    m = await store_memory(USER_A, content="Protected", db_path=TEST_DB)
    ok = await delete_memory(m.id, USER_B, db_path=TEST_DB)
    assert ok is False
    mems = await list_memories(USER_A, db_path=TEST_DB)
    assert len(mems) == 1


@pytest.mark.asyncio
async def test_delete_memory_not_found():
    ok = await delete_memory("fake-id", USER_A, db_path=TEST_DB)
    assert ok is False


@pytest.mark.asyncio
async def test_clear_memories():
    await store_memory(USER_A, content="Mem 1", db_path=TEST_DB)
    await store_memory(USER_A, content="Mem 2", db_path=TEST_DB)
    await store_memory(USER_A, content="Mem 3", db_path=TEST_DB)
    count = await clear_memories(USER_A, db_path=TEST_DB)
    assert count == 3
    mems = await list_memories(USER_A, db_path=TEST_DB)
    assert len(mems) == 0


@pytest.mark.asyncio
async def test_clear_memories_user_isolation():
    """Clearing user A's memories should not affect user B."""
    await store_memory(USER_A, content="Alice", db_path=TEST_DB)
    await store_memory(USER_B, content="Bob", db_path=TEST_DB)
    await clear_memories(USER_A, db_path=TEST_DB)
    assert len(await list_memories(USER_A, db_path=TEST_DB)) == 0
    assert len(await list_memories(USER_B, db_path=TEST_DB)) == 1


# ══════════════════════════════════════════════════════════
#  MEMORY RETRIEVAL (RAG)
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retrieve_memories_by_keyword():
    """Should return memories with matching keywords."""
    await store_memory(USER_A, content="User prefers sales data in table format", category="preference", db_path=TEST_DB)
    await store_memory(USER_A, content="User works in engineering department", category="context", db_path=TEST_DB)
    await store_memory(USER_A, content="User's timezone is EST", category="fact", db_path=TEST_DB)
    results = await retrieve_memories(USER_A, "show me sales data", db_path=TEST_DB)
    # The sales/table memory should rank highest
    assert len(results) >= 1
    contents = [r.content for r in results]
    assert any("sales" in c.lower() for c in contents)


@pytest.mark.asyncio
async def test_retrieve_memories_empty():
    results = await retrieve_memories(USER_A, "anything", db_path=TEST_DB)
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_memories_updates_access_count():
    """Retrieving a memory should increment its access_count."""
    await store_memory(USER_A, content="Important sales preference", importance=0.9, db_path=TEST_DB)
    before = await list_memories(USER_A, db_path=TEST_DB)
    assert before[0].access_count == 0
    await retrieve_memories(USER_A, "sales preference", db_path=TEST_DB)
    after = await list_memories(USER_A, db_path=TEST_DB)
    assert after[0].access_count == 1


@pytest.mark.asyncio
async def test_retrieve_memories_high_importance_always_returned():
    """Memories with importance >= 0.8 should be returned even without keyword match."""
    await store_memory(USER_A, content="Critical system setting", importance=0.9, db_path=TEST_DB)
    results = await retrieve_memories(USER_A, "completely unrelated query", db_path=TEST_DB)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_retrieve_memories_user_isolation():
    """User A should not retrieve User B's memories."""
    await store_memory(USER_A, content="Alice's secret preference about sales", db_path=TEST_DB)
    await store_memory(USER_B, content="Bob's secret preference about sales", db_path=TEST_DB)
    a_results = await retrieve_memories(USER_A, "sales preference", db_path=TEST_DB)
    b_results = await retrieve_memories(USER_B, "sales preference", db_path=TEST_DB)
    a_ids = {r.id for r in a_results}
    b_ids = {r.id for r in b_results}
    assert a_ids.isdisjoint(b_ids)


@pytest.mark.asyncio
async def test_retrieve_memories_respects_top_k():
    """Should return at most top_k results."""
    for i in range(10):
        await store_memory(USER_A, content=f"Sales metric number {i}", importance=0.9, db_path=TEST_DB)
    results = await retrieve_memories(USER_A, "sales metric", top_k=3, db_path=TEST_DB)
    assert len(results) <= 3


# ══════════════════════════════════════════════════════════
#  KEYWORD EXTRACTION & SIMILARITY
# ══════════════════════════════════════════════════════════

def test_extract_keywords_basic():
    kw = _extract_keywords("Show me total sales this month")
    assert "sales" in kw
    assert "total" in kw
    assert "month" in kw
    # Stop words removed
    assert "me" not in kw
    assert "show" not in kw
    assert "this" not in kw


def test_extract_keywords_empty():
    kw = _extract_keywords("")
    assert kw == set()


def test_extract_keywords_only_stop_words():
    kw = _extract_keywords("the a an is are was to of in for")
    assert kw == set()


def test_keyword_similarity_identical():
    a = {"sales", "revenue", "monthly"}
    assert _keyword_similarity(a, a) == 1.0


def test_keyword_similarity_no_overlap():
    a = {"sales", "revenue"}
    b = {"employees", "department"}
    assert _keyword_similarity(a, b) == 0.0


def test_keyword_similarity_partial():
    a = {"sales", "revenue", "monthly"}
    b = {"sales", "monthly", "total"}
    sim = _keyword_similarity(a, b)
    # Jaccard: 2/4 = 0.5
    assert sim == 0.5


def test_keyword_similarity_empty():
    assert _keyword_similarity(set(), {"a", "b"}) == 0.0
    assert _keyword_similarity({"a"}, set()) == 0.0
    assert _keyword_similarity(set(), set()) == 0.0


# ══════════════════════════════════════════════════════════
#  MEMORY EXTRACTION FROM EXCHANGES
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_extract_preference():
    """Should extract a memory when user mentions a preference."""
    mems = await extract_memories_from_exchange(
        USER_A,
        "I prefer to see sales data in table format",
        "Sure, I'll show it in a table.",
        db_path=TEST_DB,
    )
    assert len(mems) == 1
    assert mems[0].category == "preference"


@pytest.mark.asyncio
async def test_extract_instruction():
    """Should extract an instruction-type memory."""
    mems = await extract_memories_from_exchange(
        USER_A,
        "Remember that I only care about Q4 results",
        "Got it!",
        db_path=TEST_DB,
    )
    assert len(mems) == 1
    assert mems[0].category == "instruction"


@pytest.mark.asyncio
async def test_extract_context():
    """Should extract user context information."""
    mems = await extract_memories_from_exchange(
        USER_A,
        "I work in the engineering department",
        "Noted!",
        db_path=TEST_DB,
    )
    assert len(mems) == 1
    assert mems[0].category == "context"


@pytest.mark.asyncio
async def test_extract_nothing_for_plain_query():
    """Regular questions should not trigger memory extraction."""
    mems = await extract_memories_from_exchange(
        USER_A,
        "How many employees are there?",
        "There are 25 employees.",
        db_path=TEST_DB,
    )
    assert len(mems) == 0


@pytest.mark.asyncio
async def test_extract_only_one_per_message():
    """Even if multiple patterns match, only one memory should be extracted per message."""
    mems = await extract_memories_from_exchange(
        USER_A,
        "I prefer table format, remember that I always want sales data",
        "OK!",
        db_path=TEST_DB,
    )
    assert len(mems) == 1


# ══════════════════════════════════════════════════════════
#  FORMAT & TITLE HELPERS
# ══════════════════════════════════════════════════════════

def test_format_memories_for_prompt_empty():
    result = format_memories_for_prompt([])
    assert result == ""


def test_format_memories_for_prompt_with_data():
    mem = UserMemory(
        id="1", user_id="u1", category="preference",
        content="Prefers tables", source="auto", importance=0.7,
        created_at="2025-01-01", last_accessed="2025-01-01",
    )
    result = format_memories_for_prompt([mem])
    assert "[User Context" in result
    assert "Preference" in result
    assert "Prefers tables" in result


def test_generate_session_title_basic():
    title = generate_session_title("show me total sales this month")
    assert title  # Non-empty
    assert len(title) <= 65  # 60 + potential "..."


def test_generate_session_title_strips_prefix():
    title = generate_session_title("Show me total sales this month")
    # Should strip "Show me " and capitalize
    assert title.startswith("Total sales")


def test_generate_session_title_truncates():
    long_msg = "x" * 200
    title = generate_session_title(long_msg)
    assert len(title) <= 65


def test_generate_session_title_empty():
    title = generate_session_title("")
    assert title == "New Chat"


def test_generate_session_title_only_prefix():
    # "show me " strips to "show me" which doesn't match prefix "show me " (trailing space)
    title = generate_session_title("show me ")
    assert title == "Show me"
    # But with trailing content removed entirely:
    title2 = generate_session_title("  ")
    assert title2 == "New Chat"
