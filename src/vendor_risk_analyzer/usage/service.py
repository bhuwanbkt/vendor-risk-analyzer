"""Record and summarize Gemini token usage per company.

Writes are best-effort, like audit events: each uses its own short session
so a failed write is logged but never breaks an assessment or a chat
answer. Usage is recorded even when the request later fails (for example
an ungrounded answer), because the tokens were still spent.

Cost is an estimate from prices you configure; Gemini's free tier costs
nothing, so with no prices set only token counts are shown.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from vendor_risk_analyzer.db.models import LlmUsageEvent
from vendor_risk_analyzer.usage.tokens import TokenUsage

logger = logging.getLogger("uvicorn.error")

FEATURE_LABELS = {
    "assessment": "Assessments",
    "chat": "Chat",
    "mcp_chat": "Chat (MCP)",
}


def default_session_factory():
    # Imported lazily so this module has no database side effects on import.
    from vendor_risk_analyzer.db.session import AsyncSessionLocal

    return AsyncSessionLocal()


# Replaceable in tests.
session_factory = default_session_factory


async def record_usage(
    *,
    tenant_id: UUID | None,
    vendor_id: UUID | str | None,
    feature: str,
    model: str,
    usage: TokenUsage | None,
    reference_id=None,
    actor_subject: str | None = None,
    factory=None,
) -> None:
    if usage is None or usage.requests == 0:
        return
    try:
        async with (factory or session_factory)() as db:
            db.add(
                LlmUsageEvent(
                    tenant_id=tenant_id,
                    vendor_id=UUID(str(vendor_id)) if vendor_id else None,
                    feature=feature[:32],
                    model=(model or "unknown")[:100],
                    reference_id=str(reference_id)[:64] if reference_id else None,
                    actor_subject=actor_subject,
                    requests=usage.requests,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thought_tokens=usage.thought_tokens,
                    total_tokens=usage.total_tokens,
                )
            )
            await db.commit()
    except Exception:
        logger.exception(
            "LLM usage could not be recorded. feature=%s model=%s",
            feature,
            model,
        )


async def record_chat_usage(
    chat_service, access, *, vendor_id, reference_id, feature: str
) -> None:
    """Record tokens spent on a chat answer, including rejected ones."""
    usage = getattr(chat_service, "token_usage", None)
    if not isinstance(usage, TokenUsage):
        return
    await record_usage(
        tenant_id=access.tenant.id,
        vendor_id=vendor_id,
        feature=feature,
        model=getattr(chat_service, "model", "unknown"),
        usage=usage,
        reference_id=reference_id,
        actor_subject=access.member.subject,
    )


def _price(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} cannot be negative.")
    return value


def configured_prices() -> tuple[float, float] | None:
    """USD per million input and output tokens, when both are set."""
    input_price = _price("LLM_INPUT_PRICE_PER_MILLION_TOKENS")
    output_price = _price("LLM_OUTPUT_PRICE_PER_MILLION_TOKENS")
    if input_price is None or output_price is None:
        return None
    return input_price, output_price


def estimate_cost(usage: TokenUsage, prices: tuple[float, float] | None) -> float | None:
    if prices is None:
        return None
    input_price, output_price = prices
    return (
        usage.input_tokens * input_price
        + usage.billable_output_tokens * output_price
    ) / 1_000_000


@dataclass
class UsageSummaryRow:
    feature: str
    label: str
    model: str
    usage: TokenUsage
    estimated_cost: float | None


@dataclass
class UsageSummary:
    since: datetime
    days: int
    rows: list[UsageSummaryRow] = field(default_factory=list)
    total: TokenUsage = field(default_factory=TokenUsage)
    estimated_cost: float | None = None
    priced: bool = False


async def summarize_usage(db, *, tenant_id: UUID, days: int = 30) -> UsageSummary:
    """Token totals for one company over the last ``days``, by feature and model."""
    from sqlalchemy import func, select

    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = (
        select(
            LlmUsageEvent.feature,
            LlmUsageEvent.model,
            func.coalesce(func.sum(LlmUsageEvent.requests), 0),
            func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.thought_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0),
        )
        .where(
            LlmUsageEvent.tenant_id == tenant_id,
            LlmUsageEvent.created_at >= since,
        )
        .group_by(LlmUsageEvent.feature, LlmUsageEvent.model)
        .order_by(LlmUsageEvent.feature, LlmUsageEvent.model)
    )
    prices = configured_prices()
    summary = UsageSummary(since=since, days=days, priced=prices is not None)
    for feature, model, requests, input_t, output_t, thought_t, total_t in (
        await db.execute(query)
    ).all():
        usage = TokenUsage(
            input_tokens=int(input_t),
            output_tokens=int(output_t),
            thought_tokens=int(thought_t),
            total_tokens=int(total_t),
            requests=int(requests),
        )
        summary.rows.append(
            UsageSummaryRow(
                feature=feature,
                label=FEATURE_LABELS.get(feature, feature),
                model=model,
                usage=usage,
                estimated_cost=estimate_cost(usage, prices),
            )
        )
        summary.total = summary.total + usage
    summary.estimated_cost = estimate_cost(summary.total, prices)
    return summary
