"""
Tests for the Prompt Builder module — all prompt types.
"""

from query_engine.prompt_builder import (
    build_sql_prompt,
    build_summary_prompt,
    build_multi_step_sql_prompt,
    build_text_processing_prompt,
    build_multi_step_summary_prompt,
)


class TestBuildSQLPrompt:

    def test_returns_two_messages(self):
        messages = build_sql_prompt("TABLE: employees\n  - id", "How many employees?")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_schema_in_system_message(self):
        schema = "TABLE: sales\n  - id (integer)\n  - amount (numeric)"
        messages = build_sql_prompt(schema, "Total sales?")
        assert "sales" in messages[0]["content"]
        assert "amount" in messages[0]["content"]

    def test_question_in_user_message(self):
        messages = build_sql_prompt("TABLE: x", "Show me last month's revenue")
        assert "last month's revenue" in messages[1]["content"]

    def test_rules_present_in_system_prompt(self):
        messages = build_sql_prompt("TABLE: x", "test")
        system = messages[0]["content"]
        assert "SELECT" in system
        assert "DELETE" in system  # Mentioned as forbidden


class TestBuildSummaryPrompt:

    def test_returns_two_messages(self):
        data = [{"month": "2025-09", "revenue": 100000}]
        messages = build_summary_prompt("Revenue?", "SELECT ...", data)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_empty_data_shows_no_results(self):
        messages = build_summary_prompt("Revenue?", "SELECT ...", [])
        assert "(no results)" in messages[1]["content"]

    def test_data_included_in_user_message(self):
        data = [{"name": "Alice", "dept": "Sales"}]
        messages = build_summary_prompt("Who?", "SELECT ...", data)
        assert "Alice" in messages[1]["content"]
        assert "Sales" in messages[1]["content"]

    def test_row_count_in_message(self):
        data = [{"id": i} for i in range(5)]
        messages = build_summary_prompt("Count?", "SELECT ...", data)
        assert "5 rows" in messages[1]["content"]


class TestBuildMultiStepSQLPrompt:

    def test_returns_two_messages(self):
        messages = build_multi_step_sql_prompt("TABLE: t", "Complex question?")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_schema_included(self):
        schema = "TABLE: expenses\n  - amount (numeric)"
        messages = build_multi_step_sql_prompt(schema, "Forecast cash flow")
        assert "expenses" in messages[0]["content"]

    def test_chain_of_thought_instructions(self):
        messages = build_multi_step_sql_prompt("TABLE: t", "question")
        system = messages[0]["content"]
        assert "CHAIN-OF-THOUGHT" in system or "MULTI-STEP" in system
        assert "CTE" in system

    def test_question_in_user_message(self):
        messages = build_multi_step_sql_prompt("TABLE: t", "If sales drop 10%, what happens?")
        assert "10%" in messages[1]["content"]


class TestBuildTextProcessingPrompt:

    def test_returns_two_messages(self):
        messages = build_text_processing_prompt("Write an email")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_has_corporate_context(self):
        messages = build_text_processing_prompt("Write a memo")
        system = messages[0]["content"]
        assert "professional" in system.lower()
        assert "ATL Corp" in system

    def test_question_passed_as_user_message(self):
        q = "Polish this email: Dear Sir, we are late on delivery."
        messages = build_text_processing_prompt(q)
        assert q == messages[1]["content"]

    def test_no_sql_references(self):
        messages = build_text_processing_prompt("Summarize meeting notes")
        system = messages[0]["content"]
        # Text processing prompt should NOT talk about SQL generation
        assert "SELECT" not in system


class TestBuildMultiStepSummaryPrompt:

    def test_returns_two_messages(self):
        data = [{"dept": "Sales", "profit": 100000}]
        messages = build_multi_step_summary_prompt("Question?", "SELECT ...", data)
        assert len(messages) == 2

    def test_empty_data_handled(self):
        messages = build_multi_step_summary_prompt("Q?", "SELECT ...", [])
        assert "(no results)" in messages[1]["content"]

    def test_step_by_step_instruction(self):
        data = [{"metric": "value"}]
        messages = build_multi_step_summary_prompt("Q?", "SELECT ...", data)
        system = messages[0]["content"]
        assert "step" in system.lower() or "section" in system.lower()

    def test_data_included(self):
        data = [{"name": "Sales", "amount": 150000}]
        messages = build_multi_step_summary_prompt("Q?", "SQL", data)
        assert "150000" in messages[1]["content"]

