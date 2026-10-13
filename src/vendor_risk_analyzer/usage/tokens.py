"""Token counts reported by Gemini, in one shape for both APIs we call.

Gemini bills thinking tokens at the output rate, so cost estimates treat
``thought_tokens`` as output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _count(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thought_tokens=self.thought_tokens + other.thought_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            requests=self.requests + other.requests,
        )

    @property
    def billable_output_tokens(self) -> int:
        return self.output_tokens + self.thought_tokens

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def _build(cls, input_tokens, output_tokens, thought_tokens, total) -> "TokenUsage":
        input_tokens = _count(input_tokens)
        output_tokens = _count(output_tokens)
        thought_tokens = _count(thought_tokens)
        total = _count(total) or input_tokens + output_tokens + thought_tokens
        return cls(input_tokens, output_tokens, thought_tokens, total, requests=1)

    @classmethod
    def from_interaction(cls, interaction: Any) -> "TokenUsage":
        """Usage from the google-genai Interactions API response."""
        usage = getattr(interaction, "usage", None)
        if usage is None:
            return cls(requests=1)
        return cls._build(
            getattr(usage, "total_input_tokens", 0),
            getattr(usage, "total_output_tokens", 0),
            getattr(usage, "total_thought_tokens", 0),
            getattr(usage, "total_tokens", 0),
        )

    @classmethod
    def from_generate_content(cls, body: Any) -> "TokenUsage":
        """Usage from a REST ``generateContent`` JSON body."""
        metadata = body.get("usageMetadata") if isinstance(body, dict) else None
        if not isinstance(metadata, dict):
            return cls(requests=1)
        return cls._build(
            metadata.get("promptTokenCount"),
            metadata.get("candidatesTokenCount"),
            metadata.get("thoughtsTokenCount"),
            metadata.get("totalTokenCount"),
        )
