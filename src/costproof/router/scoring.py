"""Deterministic prompt complexity scoring."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from costproof.router.models import ComplexityResult


def _term_pattern(term: str) -> str:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return rf"(?<![\w-]){escaped}(?![\w-])"


_FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_CODE_KEYWORD_PATTERN = re.compile(
    r"\b(def|class|function|interface|return|import|from|if|elif|else|for|while|try|except|"
    r"select|insert|update|delete|const|let|var)\b",
    re.IGNORECASE,
)
_CODE_PUNCTUATION_LINE_PATTERN = re.compile(r"[{};]\s*$", re.MULTILINE)
_CODE_LEXEME_PATTERN = re.compile(r"\w+|[^\s\w]")
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
    "tradeoffs",
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
_REASONING_PATTERN = re.compile(
    "|".join(_term_pattern(keyword) for keyword in _REASONING_KEYWORDS),
    re.IGNORECASE,
)
_STRUCTURED_OUTPUT_PATTERN = re.compile(
    "|".join(_term_pattern(keyword) for keyword in _STRUCTURED_OUTPUT_KEYWORDS),
    re.IGNORECASE,
)
_STRUCTURED_OUTPUT_NEGATION_PATTERN = re.compile(
    r"(?<![\w-])(?:do\s+not|don't|dont|not|no|without|avoid|never)\s+"
    r"(?:(?:return|respond|output|emit|format|use|include)(?:\s+(?:as|in|with))?\s+)?"
    r"(?:json|schema|csv|table|yaml|xml|structured\s+output)(?![\w-])"
    r"|"
    r"(?<![\w-])(?:json|schema|csv|table|yaml|xml|structured\s+output)(?![\w-])"
    r"\s+(?:is|are)\s+not\s+(?:required|needed|wanted|allowed)",
    re.IGNORECASE,
)

_BASELINE_SCORE = 0.05
_MAX_LENGTH_SCORE = 0.25
_CODE_SCORE = 0.20
_REASONING_SCORE = 0.18
_STRUCTURED_OUTPUT_SCORE = 0.12
_MULTI_STEP_SCORE = 0.10
_GOVERNANCE_SCORE = 0.10


def approximate_token_count(text: str) -> int:
    """Return a fast local token estimate without provider calls."""

    if not text:
        return 0

    code_detected = _has_code(text)
    by_chars = math.ceil(len(text) / (3 if code_detected else 4))
    by_words = math.ceil(len(text.split()) * 1.25)
    by_code_lexemes = len(_CODE_LEXEME_PATTERN.findall(text)) if code_detected else 0
    return max(1, by_chars, by_words, by_code_lexemes)


def extract_prompt_text(
    payload: Mapping[str, object],
    *,
    include_system_messages: bool = True,
) -> str:
    """Extract text from common OpenAI-compatible payload shapes.

    System messages are included by default because providers charge for them and they can
    contain output-format constraints. Callers with long static boilerplate can opt out.
    """

    fragments: list[str] = []
    messages = payload.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            if isinstance(message, Mapping):
                role = message.get("role")
                if role == "system" and not include_system_messages:
                    continue
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

    def __init__(self, *, include_system_messages: bool = True) -> None:
        self.include_system_messages = include_system_messages

    def score_payload(self, payload: Mapping[str, object]) -> ComplexityResult:
        """Score an OpenAI-compatible payload."""

        text = extract_prompt_text(
            payload,
            include_system_messages=self.include_system_messages,
        )
        return self.score_text(text)

    def score_text(self, text: str) -> ComplexityResult:
        """Score plain prompt text on a 0.0-1.0 scale."""

        lower = text.lower()
        tokens = approximate_token_count(text)
        score = _BASELINE_SCORE if text.strip() else 0.0
        signals: list[str] = []

        # Weights intentionally sum to 1.0 including the non-empty baseline so each signal
        # remains distinguishable before the final clamp.
        length_score = min(_MAX_LENGTH_SCORE, tokens / 6000)
        if length_score >= 0.04:
            score += length_score
            signals.append(f"length:{tokens}_tokens")

        if _has_code(text):
            score += _CODE_SCORE
            signals.append("code_detected")

        if _REASONING_PATTERN.search(lower):
            score += _REASONING_SCORE
            signals.append("reasoning_language")

        if _has_structured_output_request(lower):
            score += _STRUCTURED_OUTPUT_SCORE
            signals.append("structured_output")

        prose_text = _strip_fenced_code_blocks(text)
        instruction_markers = len(re.findall(r"(^|\n)\s*(\d+\.|-|\*)\s+", prose_text))
        if instruction_markers >= 3:
            score += _MULTI_STEP_SCORE
            signals.append("multi_step_instructions")

        if "must" in lower and ("policy" in lower or "compliance" in lower or "audit" in lower):
            score += _GOVERNANCE_SCORE
            signals.append("governance_constraints")

        bounded = round(min(1.0, max(0.0, score)), 4)
        if not signals:
            signals.append("empty_prompt" if not text.strip() else "simple_prompt")

        return ComplexityResult(score=bounded, prompt_tokens=tokens, signals=tuple(signals))


def _has_code(text: str) -> bool:
    if _FENCED_CODE_PATTERN.search(text):
        return True
    if _CODE_KEYWORD_PATTERN.search(text):
        return True
    return len(_CODE_PUNCTUATION_LINE_PATTERN.findall(text)) >= 2


def _strip_fenced_code_blocks(text: str) -> str:
    return _FENCED_CODE_PATTERN.sub("", text)


def _has_structured_output_request(text: str) -> bool:
    if not _STRUCTURED_OUTPUT_PATTERN.search(text):
        return False
    return not _STRUCTURED_OUTPUT_NEGATION_PATTERN.search(text)
