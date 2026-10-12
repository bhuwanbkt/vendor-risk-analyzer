"""Score an assessment against a hand-labelled evaluation case.

A case lists the issues planted in its fixture documents. Each expected
finding names the category, the finding types that count as a match, and
keywords that must appear in the finding's title or description. Each
actual finding can satisfy at most one expectation.

Metrics per run:

- recall: share of expected findings that were found
- false positives: findings in categories the case marks as clean
- coverage mismatches: categories whose coverage status is not allowed
- overall risk: whether the policy's overall risk matches the case

This module is dependency-free so it can be unit tested without a model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class ExpectedFinding:
    id: str
    category: str
    types: list[str]
    keywords_any: list[str] = field(default_factory=list)
    keywords_all: list[str] = field(default_factory=list)
    description: str = ""

    def matches(self, finding: dict[str, Any]) -> bool:
        if finding.get("category") != self.category:
            return False
        if finding.get("finding_type") not in self.types:
            return False
        text = f"{finding.get('title', '')} {finding.get('description', '')}".lower()
        if self.keywords_all and not all(k.lower() in text for k in self.keywords_all):
            return False
        if self.keywords_any and not any(k.lower() in text for k in self.keywords_any):
            return False
        return True


@dataclass
class EvalCase:
    name: str
    documents: list[str]
    expected_findings: list[ExpectedFinding]
    clean_categories: list[str] = field(default_factory=list)
    expected_coverage: dict[str, list[str]] = field(default_factory=dict)
    expected_overall_risk: list[str] = field(default_factory=list)
    min_recall: float = 0.8
    max_false_positives: int = 0


@dataclass
class CaseResult:
    case: str
    recall: float
    matched: list[str]
    missed: list[str]
    false_positives: list[str]
    unmatched_findings: list[str]
    coverage_mismatches: list[str]
    overall_risk: str | None
    overall_risk_ok: bool
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cases(path: str | Path) -> dict[str, EvalCase]:
    raw = json.loads(Path(path).read_text())
    cases = {}
    for name, data in raw.items():
        cases[name] = EvalCase(
            name=name,
            documents=data.get("documents", []),
            expected_findings=[ExpectedFinding(**item) for item in data["expected_findings"]],
            clean_categories=data.get("clean_categories", []),
            expected_coverage=data.get("expected_coverage", {}),
            expected_overall_risk=data.get("expected_overall_risk", []),
            min_recall=data.get("min_recall", 0.8),
            max_false_positives=data.get("max_false_positives", 0),
        )
    return cases


def score_case(
    case: EvalCase,
    *,
    findings: Iterable[dict[str, Any]],
    coverage: Iterable[dict[str, Any]],
    overall_risk: str | None,
) -> CaseResult:
    findings = list(findings)
    available = list(range(len(findings)))
    matched: list[str] = []
    missed: list[str] = []

    for expected in case.expected_findings:
        hit = next((i for i in available if expected.matches(findings[i])), None)
        if hit is None:
            missed.append(expected.id)
        else:
            matched.append(expected.id)
            available.remove(hit)

    false_positives = [
        f"{findings[i].get('category')}: {findings[i].get('title')}"
        for i in available
        if findings[i].get("category") in case.clean_categories
    ]
    unmatched = [
        f"{findings[i].get('category')}: {findings[i].get('title')}"
        for i in available
        if findings[i].get("category") not in case.clean_categories
    ]

    statuses = {item.get("category"): item.get("status") for item in coverage}
    coverage_mismatches = [
        f"{category}: expected {'/'.join(allowed)}, got {statuses.get(category)}"
        for category, allowed in case.expected_coverage.items()
        if statuses.get(category) not in allowed
    ]

    total = len(case.expected_findings)
    recall = round(len(matched) / total, 3) if total else 1.0
    risk_ok = (
        not case.expected_overall_risk
        or overall_risk in case.expected_overall_risk
    )
    passed = (
        recall >= case.min_recall
        and len(false_positives) <= case.max_false_positives
        and not coverage_mismatches
        and risk_ok
    )

    return CaseResult(
        case=case.name,
        recall=recall,
        matched=matched,
        missed=missed,
        false_positives=false_positives,
        unmatched_findings=unmatched,
        coverage_mismatches=coverage_mismatches,
        overall_risk=overall_risk,
        overall_risk_ok=risk_ok,
        passed=passed,
    )


def summarize_runs(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate repeated runs of one case (the model is not deterministic)."""
    runs = len(results)
    if not runs:
        return {}
    expected_ids: dict[str, int] = {}
    for result in results:
        for item in result.matched:
            expected_ids[item] = expected_ids.get(item, 0) + 1
        for item in result.missed:
            expected_ids.setdefault(item, 0)
    return {
        "case": results[0].case,
        "runs": runs,
        "pass_rate": round(sum(r.passed for r in results) / runs, 3),
        "mean_recall": round(sum(r.recall for r in results) / runs, 3),
        "detection_rate": {
            item: round(count / runs, 3) for item, count in sorted(expected_ids.items())
        },
        "false_positive_runs": sum(bool(r.false_positives) for r in results),
    }


def format_result(result: CaseResult) -> str:
    lines = [
        f"{'PASS' if result.passed else 'FAIL'}  {result.case}: "
        f"recall {result.recall:.0%}, overall risk {result.overall_risk or 'none'}"
        f"{'' if result.overall_risk_ok else ' (unexpected)'}",
    ]
    for item in result.matched:
        lines.append(f"  found   {item}")
    for item in result.missed:
        lines.append(f"  MISSED  {item}")
    for item in result.false_positives:
        lines.append(f"  FALSE+  {item}")
    for item in result.coverage_mismatches:
        lines.append(f"  COVER   {item}")
    for item in result.unmatched_findings:
        lines.append(f"  extra   {item}")
    return "\n".join(lines)
