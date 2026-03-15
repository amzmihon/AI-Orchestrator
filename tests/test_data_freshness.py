"""
Tests for the data_freshness module and the /api/data-freshness endpoint.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from data_freshness import (
    get_data_freshness,
    _get_latest_timestamp,
    FRESH_THRESHOLD,
    STALE_THRESHOLD,
    _FRESHNESS_TABLES,
)


# ── Unit tests: _get_latest_timestamp ─────────────────

@pytest.mark.asyncio
async def test_get_latest_timestamp_success():
    """When the DB returns a row, it should extract the timestamp."""
    now = datetime.now(timezone.utc)
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, idx: now

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = mock_result
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn

    with patch("data_freshness.engine", mock_engine):
        result = await _get_latest_timestamp("sales", "created_at")
        assert result == now


@pytest.mark.asyncio
async def test_get_latest_timestamp_empty_table():
    """When MAX returns None (empty table), should return None."""
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, idx: None

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = mock_result
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn

    with patch("data_freshness.engine", mock_engine):
        result = await _get_latest_timestamp("sales", "created_at")
        assert result is None


@pytest.mark.asyncio
async def test_get_latest_timestamp_db_error():
    """When the DB connection fails, should return None (not raise)."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("Connection refused")

    with patch("data_freshness.engine", mock_engine):
        result = await _get_latest_timestamp("sales", "created_at")
        assert result is None


# ── Unit tests: get_data_freshness ────────────────────

@pytest.mark.asyncio
async def test_freshness_all_tables_recent():
    """When all tables have data within FRESH_THRESHOLD, status should be 'fresh'."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=10)

    async def mock_get_ts(table, column):
        return recent

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "fresh"
        assert result["minutes_ago"] <= 11  # Allow for test execution time
        assert "10 minute" in result["message"] or "just now" in result["message"] or "11 minute" in result["message"]
        assert result["last_updated"] is not None
        assert result["sync_interval_minutes"] == 30


@pytest.mark.asyncio
async def test_freshness_stale():
    """When all tables have data older than STALE_THRESHOLD, status should be 'stale'."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=3)

    async def mock_get_ts(table, column):
        return old

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "stale"
        assert result["minutes_ago"] >= 180
        assert "3 hour" in result["message"]


@pytest.mark.asyncio
async def test_freshness_slightly_stale():
    """When latest data is between FRESH and STALE thresholds."""
    now = datetime.now(timezone.utc)
    middle = now - timedelta(minutes=60)

    async def mock_get_ts(table, column):
        return middle

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "slightly_stale"
        assert result["minutes_ago"] >= 59


@pytest.mark.asyncio
async def test_freshness_unknown_when_no_data():
    """When all tables fail or return None, status should be 'unknown'."""
    async def mock_get_ts(table, column):
        return None

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "unknown"
        assert result["last_updated"] is None
        assert result["minutes_ago"] is None
        assert "Unable" in result["message"]


@pytest.mark.asyncio
async def test_freshness_mixed_tables():
    """When some tables are fresh and some are stale, overall should use the newest timestamp."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=5)
    old = now - timedelta(hours=4)

    call_count = 0

    async def mock_get_ts(table, column):
        nonlocal call_count
        call_count += 1
        # First table is recent, rest are old
        return recent if call_count == 1 else old

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        # Overall status should be based on the freshest table
        assert result["status"] == "fresh"
        assert result["minutes_ago"] <= 6


@pytest.mark.asyncio
async def test_freshness_table_details_included():
    """Each table should be listed in table_details."""
    now = datetime.now(timezone.utc)

    async def mock_get_ts(table, column):
        return now - timedelta(minutes=15)

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        for table, column in _FRESHNESS_TABLES:
            assert table in result["table_details"]
            detail = result["table_details"][table]
            assert detail["column"] == column
            assert detail["latest"] is not None
            assert detail["minutes_ago"] is not None


@pytest.mark.asyncio
async def test_freshness_just_now():
    """When data is less than 1 minute old, message should say 'just now'."""
    now = datetime.now(timezone.utc)

    async def mock_get_ts(table, column):
        return now

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "fresh"
        assert "just now" in result["message"]


@pytest.mark.asyncio
async def test_freshness_days_ago():
    """When data is older than 24 hours, message should show days."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)

    async def mock_get_ts(table, column):
        return old

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "stale"
        assert "3 days" in result["message"]


@pytest.mark.asyncio
async def test_freshness_naive_timestamp_handling():
    """Naive timestamps (no tzinfo) should be treated as UTC."""
    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None) - timedelta(minutes=20)

    async def mock_get_ts(table, column):
        return naive

    with patch("data_freshness._get_latest_timestamp", side_effect=mock_get_ts):
        result = await get_data_freshness()
        assert result["status"] == "fresh"
        assert result["minutes_ago"] is not None


# ── Integration-style: Verify all table definitions ───

def test_freshness_tables_are_valid():
    """All table/column pairs in _FRESHNESS_TABLES should be valid identifiers."""
    for table, column in _FRESHNESS_TABLES:
        assert table.isidentifier(), f"Invalid table name: {table}"
        assert column.isidentifier(), f"Invalid column name: {column}"
        assert column in ("created_at", "updated_at"), f"Unexpected column: {column}"


def test_thresholds_are_sane():
    """Thresholds must be positive and FRESH < STALE."""
    assert FRESH_THRESHOLD > 0
    assert STALE_THRESHOLD > FRESH_THRESHOLD
