from __future__ import annotations

from vendor_risk_analyzer.risk_policy.service import (
    POLICY_RULES,
    POLICY_VERSION,
    SEVERITY_RANK,
)


def test_policy_version_is_explicit() -> None:
    assert (
        POLICY_VERSION
        == "vendor-risk-policy-v3"
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


def test_every_finding_type_and_category_has_a_rule() -> None:
    from typing import get_args

    from vendor_risk_analyzer.risk_analysis.schemas import (
        FindingType,
        RiskCategory,
    )

    expected = {
        (finding_type, category)
        for finding_type in get_args(FindingType)
        for category in get_args(RiskCategory)
    }

    assert set(POLICY_RULES) == expected


def test_rules_use_valid_unique_ids_and_never_critical() -> None:
    severities = {rule["severity"] for rule in POLICY_RULES.values()}
    rule_ids = [rule["rule_id"] for rule in POLICY_RULES.values()]

    assert severities <= {"low", "medium", "high"}
    assert len(set(rule_ids)) == len(rule_ids)
    assert all(rule["rationale"].strip() for rule in POLICY_RULES.values())


def test_explicit_access_control_risk_is_high() -> None:
    rule = POLICY_RULES[("explicit_risk", "access_control")]

    assert rule["severity"] == "high"
    assert rule["rule_id"] == "VRP-AC-EXPLICIT-001"


def test_risk_score_combines_findings_with_diminishing_returns() -> None:
    from vendor_risk_analyzer.risk_policy.service import compute_risk_score

    assert compute_risk_score([]) == 0.0
    assert compute_risk_score(["unrated", "unrated"]) == 0.0
    assert compute_risk_score(["high"]) == 30.0
    assert compute_risk_score(["high", "high"]) == 51.0
    # Larkspur: 2 high, 4 medium, 1 low
    larkspur = ["high"] * 2 + ["medium"] * 4 + ["low"]
    assert compute_risk_score(larkspur) == 75.7
    assert compute_risk_score(["critical"] * 50) <= 100.0
    # Adding a finding never lowers the score.
    assert compute_risk_score(larkspur + ["low"]) > compute_risk_score(larkspur)


def test_coverage_percent_counts_assessed_categories() -> None:
    from vendor_risk_analyzer.risk_policy.service import compute_coverage_percent

    coverage = [{"status": "assessed"}] * 8 + [{"status": "insufficient_evidence"}]

    assert compute_coverage_percent(coverage) == 88.9
    assert compute_coverage_percent(None) is None
    assert compute_coverage_percent([]) is None

