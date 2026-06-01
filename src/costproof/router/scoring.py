"""Deterministic prompt complexity scoring."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from costproof.router.models import ComplexityResult


_CODE_PATTERNS = (
    re.compile(r"```"),
    re.compile(r"\b(def|class|function|interface|SELECT|INSERT|UPDATE|DELETE)\b"),
    re.compile(r"[{};]\s*$", re.MULTILINE),
)
_REASONING_KEYWORDS = (
    "analyze",
    "compare",
    "debug",
    "derive",
    "explain why",
    "multi-step",
    "reason",
    "root cause",
    "tradeoff",
)
_STRUCTURED_OUTPUT_KEYWORDS = (
    "json",
    "schema",
    "csv",
    "table",
    "yaml",
    "xml",
    "structured output",
)


def approximate_token_count(text: str) -> int:
    """Return a fast local token estimate without provider calls."""

    if not text:
        return 0
    by_chars = math.ceil(len(text) / 4)
    by_words = math.ceil(len(text.split()) * 1.25)
    return max(1, by_chars, by_words)


def extract_prompt_text(payload: Mapping[str, object]) -> str:
    """Extract user-visible text from common OpenAI-compatible payload shapes."""

    fragments: list[str] = []
    messages = payload.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            if isinstance(message, Mapping):
                fragments.append(_content_to_text(message.get("content")))

    input_value = payload.get("input")
    if input_value is not None:
        fragments.append(_content_to_text(input_value))

    prompt = payload.get("prompt")
    if prompt is not None:
        fragments.append(_content_to_text(prompt))

    return "\n".join(fragment for fragment in fragments if fragment)


def _content_to_text(value: object) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        fragments: list[str] = []
        for item in value:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    fragments.append(text)
                elif item.get("type") == "input_text":
                    nested_text = item.get("content")
                    if isinstance(nested_text, str):
                        fragments.append(nested_text)
        return "\n".join(fragments)

    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text

    return ""


class ComplexityScorer:
    """Fast, explainable rule-based complexity scorer."""

    def score_payload(self, payload: Mapping[str, object]) -> ComplexityResult:
        """Score an OpenAI-compatible payload."""

        text = extract_prompt_text(payload)
        return self.score_text(text)

    def score_text(self, text: str) -> ComplexityResult:
        """Score plain prompt text on a 0.0-1.0 scale."""

        lower = text.lower()
        tokens = approximate_token_count(text)
        score = 0.05
        signals: list[str] = []

        length_score = min(0.30, tokens / 6000)
        if length_score >= 0.04:
            score += length_score
            signals.append(f"length:{tokens}_tokens")

        if any(pattern.search(text) for pattern in _CODE_PATTERNS):
            score += 0.20
            signals.append("code_detected")

        if any(keyword in lower for keyword in _REASONING_KEYWORDS):
            score += 0.18
            signals.append("reasoning_language")

        if any(keyword in lower for keyword in _STRUCTURED_OUTPUT_KEYWORDS):
            score += 0.12
            signals.append("structured_output")

        instruction_markers = len(re.findall(r"(^|\n)\s*(\d+\.|-|\*)\s+", text))
        if instruction_markers >= 3:
            score += 0.10
            signals.append("multi_step_instructions")

        if "must" in lower and ("policy" in lower or "compliance" in lower or "audit" in lower):
            score += 0.10
            signals.append("governance_constraints")

        bounded = round(min(1.0, max(0.0, score)), 4)
        if not signals:
            signals.append("simple_prompt")

        return ComplexityResult(score=bounded, prompt_tokens=tokens, signals=tuple(signals))
