"""Tests for extractor.py — Haiku fact extractor (no real API calls)."""

import pytest
from extractor import format_conversation, filter_by_quality, QUALITY_THRESHOLD


# ---------------------------------------------------------------------------
# format_conversation
# ---------------------------------------------------------------------------

def test_format_conversation():
    """Basic user + assistant turn formatting."""
    lines = [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello, what is the capital of France?"}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "The capital of France is Paris."}
                ]
            },
        },
    ]
    result = format_conversation(lines)
    assert "User: Hello, what is the capital of France?" in result
    assert "Assistant: The capital of France is Paris." in result


def test_format_conversation_skips_tool_blocks():
    """tool_use and tool_result blocks must be filtered out; only text survives."""
    lines = [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Run the analysis."},
                    {"type": "tool_result", "content": "some raw output"},
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "Done."},
                ]
            },
        },
    ]
    result = format_conversation(lines)
    assert "tool_use" not in result
    assert "tool_result" not in result
    assert "Run the analysis." in result
    assert "Done." in result


def test_format_conversation_empty():
    """Empty input should return an empty string."""
    assert format_conversation([]) == ""


# ---------------------------------------------------------------------------
# filter_by_quality
# ---------------------------------------------------------------------------

def test_filter_by_quality():
    """Facts with quality_score >= threshold pass; others fail."""
    facts = [
        {"text": "fact A", "quality_score": 0.9},
        {"text": "fact B", "quality_score": 0.6},   # exactly at threshold → pass
        {"text": "fact C", "quality_score": 0.59},  # just below → fail
        {"text": "fact D", "quality_score": 0.0},
    ]
    passed, failed = filter_by_quality(facts, threshold=QUALITY_THRESHOLD)

    passed_texts = [f["text"] for f in passed]
    failed_texts = [f["text"] for f in failed]

    assert "fact A" in passed_texts
    assert "fact B" in passed_texts
    assert "fact C" in failed_texts
    assert "fact D" in failed_texts
    assert len(passed) == 2
    assert len(failed) == 2
