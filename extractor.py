"""Haiku fact extractor for PonyMemory v2.

Three public functions:
  - format_conversation(lines) -> str
  - extract_facts(conversation_text, project) -> list[dict]
  - filter_by_quality(facts, threshold) -> tuple[list, list]
"""

import json
import anthropic

QUALITY_THRESHOLD = 0.6

_EXTRACT_PROMPT = (
    "分析以下对话片段，提取值得长期记忆的内容。"
    "只提取以下类型：correction/decision/milestone/finding/preference。"
    "对每个提取项返回JSON数组。"
    "如果没有值得记忆的内容返回空数组[]。"
    "不要返回markdown格式或代码块，只返回纯JSON。"
)


def format_conversation(lines: list[dict]) -> str:
    """Convert a list of JSONL transcript entry dicts to a plain text string.

    Each entry has:
        - type: "user" or "assistant"
        - message.content: list of blocks (text / tool_use / tool_result / …)

    Only text blocks are kept; all other block types are silently skipped.
    Returns "" for empty input.
    """
    if not lines:
        return ""

    parts: list[str] = []
    for entry in lines:
        role = entry.get("type", "")
        speaker = "User" if role == "user" else "Assistant"
        content_blocks = entry.get("message", {}).get("content", [])
        text_fragments = [
            block["text"]
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if text_fragments:
            parts.append(f"{speaker}: {' '.join(text_fragments)}")

    return "\n\n".join(parts)


def extract_facts(conversation_text: str, project: str) -> list[dict]:
    """Call Claude Haiku to extract memorable facts from conversation_text.

    Returns a list of dicts with keys:
        text          (str, 50-200 chars)
        memory_type   (str: correction/decision/milestone/finding/preference)
        tags          (list[str])
        quality_score (float, 0-1)

    Returns [] when:
        - conversation_text is shorter than 50 characters
        - Any API or parsing error occurs
    """
    if len(conversation_text) < 50:
        return []

    prompt = (
        f"{_EXTRACT_PROMPT}\n\n"
        f"项目：{project}\n\n"
        f"对话内容：\n{conversation_text}"
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            # Drop the opening fence line (```json or ```) and the closing ```
            inner = []
            in_block = False
            for line in lines:
                if line.startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.startswith("```") and in_block:
                    break
                if in_block:
                    inner.append(line)
            raw = "\n".join(inner)

        facts = json.loads(raw)
        if not isinstance(facts, list):
            return []
        return facts
    except Exception:
        return []


def filter_by_quality(
    facts: list[dict], threshold: float = QUALITY_THRESHOLD
) -> tuple[list[dict], list[dict]]:
    """Split facts into (passed, failed) based on quality_score >= threshold."""
    passed = [f for f in facts if f.get("quality_score", 0) >= threshold]
    failed = [f for f in facts if f.get("quality_score", 0) < threshold]
    return passed, failed
