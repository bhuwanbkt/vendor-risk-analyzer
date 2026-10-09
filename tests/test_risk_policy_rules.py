from __future__ import annotations

from vendor_risk_analyzer.risk_policy.service import (
    POLICY_RULES,
    POLICY_VERSION,
    SEVERITY_RANK,
)


def test_policy_version_is_explicit() -> None:
    assert (
        POLICY_VERSION
        == "vendor-risk-policy-v1"
    )


def test_incident_response_contradiction_rule() -> None:
    rule = POLICY_RULES[
        (
            "contradiction",
            "incident_response",
        )
    ]

    assert (
        rule["severity"]
        == "medium"
    )

    assert (
        rule["rule_id"]
        == "VRP-IR-CONTRADICTION-001"
    )


def test_business_continuity_explicit_risk_rule() -> None:
    rule = POLICY_RULES[
        (
            "explicit_risk",
            "business_continuity",
        )
    ]

    assert (
        rule["severity"]
        == "medium"
    )

    assert (
        rule["rule_id"]
        == "VRP-BC-EXPLICIT-001"
    )


def test_severity_rank_order_is_monotonic() -> None:
    ordered = [
        "unrated",
        "low",
        "medium",
        "high",
        "critical",
    ]

    ranks = [
        SEVERITY_RANK[
            severity
        ]
        for severity in ordered
    ]

    assert ranks == sorted(
        ranks
    )

    assert len(
        set(
            ranks
        )
    ) == len(
        ranks
    )
