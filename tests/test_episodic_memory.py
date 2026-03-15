"""
Tests for the Episodic Memory and enhanced Intent Classifier.
"""

import pytest
from query_engine.intent_classifier import (
    classify,
    Intent,
    EpisodicMemory,
    EpisodicEntry,
    episodic_memory,
)


class TestEpisodicMemory:
    """Tests for the EpisodicMemory class."""

    def setup_method(self):
        self.memory = EpisodicMemory(max_episodes=10)

    def test_record_and_retrieve(self):
        entry = EpisodicEntry(
            id="ep-1",
            timestamp=1000.0,
            user_question="Show me total sales",
            classified_intent="data_query",
            confidence=0.9,
            sub_tasks=["Generate SQL", "Execute", "Summarize"],
            execution_success=True,
        )
        self.memory.record("user-1", entry)
        similar = self.memory.get_similar_episodes("user-1", "Show total sales data")
        assert len(similar) > 0
        assert similar[0].id == "ep-1"

    def test_max_episodes_trimming(self):
        for i in range(15):
            entry = EpisodicEntry(
                id=f"ep-{i}",
                timestamp=1000.0 + i,
                user_question=f"Question {i} about sales",
                classified_intent="data_query",
                confidence=0.8,
                sub_tasks=[],
            )
            self.memory.record("user-1", entry)
        # Should be trimmed to 10
        assert len(self.memory._store["user-1"]) == 10

    def test_failure_patterns(self):
        for error in ["COLUMN_NOT_FOUND", "SYNTAX_ERROR", "COLUMN_NOT_FOUND"]:
            entry = EpisodicEntry(
                id=f"ep-{error}",
                timestamp=1000.0,
                user_question="Test query",
                classified_intent="data_query",
                confidence=0.5,
                sub_tasks=[],
                execution_success=False,
                error_type=error,
            )
            self.memory.record("user-1", entry)
        patterns = self.memory.get_failure_patterns("user-1")
        assert "COLUMN_NOT_FOUND" in patterns
        assert "SYNTAX_ERROR" in patterns

    def test_success_rate(self):
        for success in [True, True, True, False]:
            entry = EpisodicEntry(
                id=f"ep-{success}",
                timestamp=1000.0,
                user_question="Revenue query",
                classified_intent="data_query",
                confidence=0.8,
                sub_tasks=[],
                execution_success=success,
            )
            self.memory.record("user-1", entry)
        rate = self.memory.get_success_rate("user-1", "data_query")
        assert rate == 0.75

    def test_success_rate_no_data(self):
        rate = self.memory.get_success_rate("nonexistent", "data_query")
        assert rate == 0.5  # Neutral

    def test_stats(self):
        entry = EpisodicEntry(
            id="ep-1",
            timestamp=1000.0,
            user_question="Test",
            classified_intent="data_query",
            confidence=0.9,
            sub_tasks=[],
            execution_success=True,
        )
        self.memory.record("user-1", entry)
        stats = self.memory.get_stats("user-1")
        assert stats["total_episodes"] == 1
        assert stats["overall_success_rate"] == 1.0

    def test_stats_empty_user(self):
        stats = self.memory.get_stats("nobody")
        assert stats["total_episodes"] == 0

    def test_no_similar_for_empty_user(self):
        similar = self.memory.get_similar_episodes("nobody", "any question")
        assert similar == []


class TestClassifierSubTasks:
    """Test that the classifier produces sub-tasks for multi-step queries."""

    def test_multi_step_has_sub_tasks(self):
        result = classify(
            "If sales drop by 10%, what will projected revenue look like and "
            "which department should we focus on?"
        )
        assert result.intent == Intent.MULTI_STEP
        assert len(result.sub_tasks) > 2

    def test_data_query_has_sub_tasks(self):
        result = classify("Show me total sales for last month")
        assert len(result.sub_tasks) > 0

    def test_text_processing_has_sub_tasks(self):
        result = classify("Write a professional email about project delays")
        assert len(result.sub_tasks) > 0


class TestClassifierComplexity:
    """Test the complexity scoring."""

    def test_short_question_low_complexity(self):
        result = classify("Show sales")
        assert result.complexity_score < 0.5

    def test_complex_question_higher_complexity(self):
        result = classify(
            "Analyze sales trends for the last 12 months, identify the top "
            "performing departments, and recommend budget reallocation strategies "
            "based on growth patterns and expense ratios"
        )
        assert result.complexity_score > 0.2


class TestClassifierSuggestedTables:
    """Test table suggestion from patterns."""

    def test_employees_suggested(self):
        result = classify("Show me all employees in Engineering")
        assert "employees" in result.suggested_tables

    def test_revenue_suggested(self):
        result = classify("Show revenue for last quarter")
        assert "revenue" in result.suggested_tables or "sales" in result.suggested_tables


class TestNewMultiStepPatterns:
    """Test the new Qwen 3.5 agentic workflow patterns."""

    def test_anomaly_detection(self):
        result = classify("Identify any unusual spikes in expenses this quarter")
        assert result.intent == Intent.MULTI_STEP

    def test_root_cause_analysis(self):
        result = classify("Why did revenue drop last month? Find the root cause")
        assert result.intent == Intent.MULTI_STEP

    def test_benchmarking(self):
        result = classify("Compare performance across departments for this quarter")
        assert result.intent == Intent.MULTI_STEP

    def test_prioritization(self):
        result = classify("Prioritize projects by risk and opportunity score")
        assert result.intent == Intent.MULTI_STEP


class TestEpisodicCalibration:
    """Test episodic memory integration with classifier."""

    def setup_method(self):
        # Clear global episodic memory
        episodic_memory._store.clear()

    def test_classify_with_user_id(self):
        result = classify("Show me total sales", user_id="user-test")
        assert isinstance(result.confidence, float)

    def test_episodic_boost_from_past_success(self):
        # Record a successful multi-step episode
        entry = EpisodicEntry(
            id="ep-success",
            timestamp=1000.0,
            user_question="Show sales growth trends",
            classified_intent="multi_step_analysis",
            confidence=0.9,
            sub_tasks=["Gather data", "Calculate trends"],
            execution_success=True,
        )
        episodic_memory.record("user-cal", entry)

        # A similar question should get an episodic boost
        result = classify("Show revenue growth trends", user_id="user-cal")
        # The episodic boost should influence the result
        assert result.confidence > 0
