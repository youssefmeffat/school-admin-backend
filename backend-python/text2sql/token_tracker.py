"""Approximate token accounting.

Uses `tiktoken` when available. The encoder is created once and reused,
which avoids repeatedly initializing the tokenizer during an ask() call.
If tiktoken is unavailable, a fast ~4 characters/token heuristic is used.
"""

from dataclasses import dataclass, field
from typing import List, Optional


_ENCODER = None
_TIKTOKEN_CHECKED = False


def _get_encoder():
    """Load the tokenizer once and reuse it."""
    global _ENCODER, _TIKTOKEN_CHECKED

    if _TIKTOKEN_CHECKED:
        return _ENCODER

    _TIKTOKEN_CHECKED = True

    try:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except (ImportError, Exception):
        _ENCODER = None

    return _ENCODER


def estimate_tokens(text: str) -> int:
    """Estimate token count without repeatedly initializing tiktoken."""
    if not text:
        return 0

    encoder = _get_encoder()

    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass

    # Fast fallback: approximately 4 characters per token.
    return max(1, (len(text) + 3) // 4)


@dataclass
class TokenUsageEvent:
    stage: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class TokenTracker:
    events: List[TokenUsageEvent] = field(default_factory=list)

    def record(
        self,
        stage: str,
        prompt_text: str,
        completion_text: str = "",
    ) -> None:
        self.events.append(
            TokenUsageEvent(
                stage=stage,
                prompt_tokens=estimate_tokens(prompt_text),
                completion_tokens=estimate_tokens(completion_text),
            )
        )

    @property
    def total_tokens(self) -> int:
        return sum(
            event.prompt_tokens + event.completion_tokens
            for event in self.events
        )

    @property
    def prompt_tokens(self) -> int:
        return sum(
            event.prompt_tokens
            for event in self.events
        )

    @property
    def completion_tokens(self) -> int:
        return sum(
            event.completion_tokens
            for event in self.events
        )

    def summary(self) -> str:
        lines = [
            (
                f"{event.stage}: "
                f"{event.prompt_tokens} prompt + "
                f"{event.completion_tokens} completion"
            )
            for event in self.events
        ]

        lines.append(
            f"TOTAL (approx): {self.total_tokens} tokens"
        )

        return "\n".join(lines)

    def clear(self) -> None:
        """Reset recorded events without recreating the tracker."""
        self.events.clear()