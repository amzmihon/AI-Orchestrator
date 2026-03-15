"""
Tests for the Intent Classifier module.
"""

import pytest
from query_engine.intent_classifier import classify, Intent, ClassifiedIntent


class TestTextProcessingIntent:
    """Questions that should be classified as text_processing."""

    def test_email_writing(self):
        result = classify("Write a professional email to the client about the project delay")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_email_polishing(self):
        result = classify("Polish this email to make it more professional: Hi John, we are late on the delivery, sorry about that.")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_document_summarization(self):
        result = classify("Summarize the key deliverables from this document: " + "x " * 100)
        assert result.intent == Intent.TEXT_PROCESSING

    def test_meeting_notes_extraction(self):
        result = classify(
            "Extract action items from these meeting notes:\n"
            "- Discussed Q1 plan\n- Ahmed to finalize budget\n"
            "- Sara to review contract\n- Next meeting March 5th"
        )
        assert result.intent == Intent.TEXT_PROCESSING

    def test_rewrite_request(self):
        result = classify("Rewrite this paragraph to improve the tone and grammar: The project is not going well and we need to fix things fast.")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_draft_memo(self):
        result = classify("Draft a memo to all staff about the new remote work policy")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_translation(self):
        result = classify("Translate this announcement to Arabic")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_brainstorming(self):
        result = classify("Brainstorm 5 ideas for improving employee engagement")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_grammar_fix(self):
        result = classify("Fix the grammar and improve the wording of this report intro")
        assert result.intent == Intent.TEXT_PROCESSING

    def test_long_pasted_text(self):
        long_text = "Meeting notes from Feb 20 standup:\n" + "Point discussed. " * 50
        result = classify(f"Summarize this: {long_text}")
        assert result.intent == Intent.TEXT_PROCESSING


class TestDataQueryIntent:
    """Questions that should be classified as data_query."""

    def test_show_sales(self):
        result = classify("Show me total sales for last month")
        assert result.intent == Intent.DATA_QUERY

    def test_count_employees(self):
        result = classify("How many employees are in the Engineering department?")
        assert result.intent == Intent.DATA_QUERY

    def test_top_sales_reps(self):
        result = classify("Who are the top 5 sales reps by revenue?")
        assert result.intent == Intent.DATA_QUERY

    def test_attendance_report(self):
        result = classify("Give me an attendance report for last week")
        assert result.intent == Intent.DATA_QUERY

    def test_customer_list(self):
        result = classify("List all customers in the Middle East region")
        assert result.intent == Intent.DATA_QUERY

    def test_revenue_breakdown(self):
        result = classify("Show me a revenue breakdown by department for this quarter")
        # This has cross-departmental comparison signals, so multi-step is acceptable
        assert result.intent in (Intent.DATA_QUERY, Intent.MULTI_STEP)

    def test_leave_requests(self):
        result = classify("Show me all pending leave requests")
        assert result.intent == Intent.DATA_QUERY

    def test_project_list(self):
        result = classify("List active projects with their budgets")
        assert result.intent == Intent.DATA_QUERY

    def test_average_salary(self):
        result = classify("What is the average salary per department?")
        assert result.intent == Intent.DATA_QUERY

    def test_expense_summary(self):
        result = classify("Show me total expenses for January")
        assert result.intent == Intent.DATA_QUERY


class TestMultiStepIntent:
    """Questions that should be classified as multi_step_analysis."""

    def test_what_if_sales_drop(self):
        result = classify("If our sales drop by 10% next month, what will our projected cash flow look like?")
        assert result.intent == Intent.MULTI_STEP

    def test_growth_analysis(self):
        result = classify("What is our company growth over the last year, and which department contributed the most to profit?")
        assert result.intent == Intent.MULTI_STEP

    def test_leave_coverage_analysis(self):
        result = classify("Check if there are crucial projects running in March. If I approve this leave, who can cover the tasks?")
        assert result.intent == Intent.MULTI_STEP

    def test_yoy_comparison(self):
        result = classify("How do this week's sales compare to the same week last year?")
        assert result.intent == Intent.MULTI_STEP

    def test_financial_forecast(self):
        result = classify("Forecast our cash flow for next quarter based on current expenses and revenue trends")
        assert result.intent == Intent.MULTI_STEP

    def test_runway_calculation(self):
        result = classify("Calculate our financial runway given current burn rate and cash reserves")
        assert result.intent == Intent.MULTI_STEP

    def test_trend_analysis(self):
        result = classify("Show me the month-over-month growth trend in sales revenue")
        assert result.intent == Intent.MULTI_STEP

    def test_correlation_question(self):
        result = classify("Is there a correlation between department budget and revenue growth?")
        assert result.intent == Intent.MULTI_STEP


class TestClassificationOutput:
    """Test the structure and properties of the classification output."""

    def test_returns_classified_intent(self):
        result = classify("Show me sales data")
        assert isinstance(result, ClassifiedIntent)
        assert isinstance(result.intent, Intent)
        assert isinstance(result.confidence, float)
        assert isinstance(result.reasoning, str)

    def test_confidence_range(self):
        for q in ["Show sales", "Write an email to the client", "If sales drop 10%"]:
            result = classify(q)
            assert 0.0 <= result.confidence <= 1.0

    def test_reasoning_not_empty(self):
        result = classify("Show me total revenue")
        assert len(result.reasoning) > 0

    def test_unknown_defaults_to_data_query(self):
        result = classify("xyz")
        assert result.intent == Intent.DATA_QUERY

    def test_ambiguous_defaults_reasonably(self):
        # A generic question with no strong signals
        result = classify("Tell me about the company")
        assert result.intent in (Intent.DATA_QUERY, Intent.TEXT_PROCESSING)


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_minimum_length_question(self):
        result = classify("hi!")
        assert isinstance(result, ClassifiedIntent)

    def test_mixed_signals_data_wins_over_text(self):
        # Has both text and data signals — data signal is stronger
        result = classify("Show me employee attendance report for last month")
        assert result.intent in (Intent.DATA_QUERY, Intent.MULTI_STEP)

    def test_quoted_text_boosts_text_processing(self):
        result = classify(
            'Polish this email: "Dear Sir, I am writing to inform you" '
            '"about the delay in our project delivery"'
        )
        assert result.intent == Intent.TEXT_PROCESSING

    def test_multi_step_with_sequential_language(self):
        result = classify(
            "First check if employee Bilal has critical projects in March, "
            "then find someone with similar skills who is available"
        )
        assert result.intent == Intent.MULTI_STEP
