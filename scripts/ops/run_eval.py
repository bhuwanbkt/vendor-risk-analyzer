"""Run the assessment quality evaluation against live vendors.

Each case is a vendor whose documents are the fixtures in evals/fixtures/.
The script runs the same retrieval + LLM analysis + policy rules as a real
assessment, without saving anything, and scores the result against the
planted issues in evaluation/cases.json.

    python scripts/ops/run_eval.py \\
        --case larkspur=<vendor-uuid> --case brightwater=<vendor-uuid> --runs 3

The model is not deterministic, so --runs N repeats each case and reports
how often each planted issue was detected. Exits 1 unless every run passes.
Each run makes live Gemini calls (about 10 embeddings and one analysis).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from vendor_risk_analyzer.db.url import get_database_url
from vendor_risk_analyzer.embeddings.service import EmbeddingService
from vendor_risk_analyzer.evaluation import DEFAULT_CASES_PATH
from vendor_risk_analyzer.evaluation.scoring import (
    format_result,
    load_cases,
    score_case,
    summarize_runs,
)
from vendor_risk_analyzer.retrieval.service import SemanticRetriever
from vendor_risk_analyzer.risk_analysis.service import RiskAnalysisService
from vendor_risk_analyzer.risk_policy.service import (
    POLICY_RULES,
    POLICY_VERSION,
    SEVERITY_RANK,
    compute_risk_score,
)


def parse_case(value: str) -> tuple[str, str]:
    name, sep, vendor_id = value.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError("Use NAME=VENDOR_UUID")
    try:
        return name.strip(), str(UUID(vendor_id.strip()))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid vendor UUID: {vendor_id}") from exc


def policy_outcome(findings) -> tuple[str | None, float]:
    severities = [
        POLICY_RULES.get((f.finding_type, f.category), {}).get("severity", "unrated")
        for f in findings
    ]
    rated = [s for s in severities if s != "unrated"]
    overall = max(rated, key=lambda s: SEVERITY_RANK[s]) if rated else None
    return overall, compute_risk_score(severities)


async def evaluate(case_args, runs: int, cases_path: str) -> dict:
    cases = load_cases(cases_path)
    unknown = [name for name, _ in case_args if name not in cases]
    if unknown:
        raise SystemExit(f"Unknown case(s): {', '.join(unknown)}. Known: {', '.join(cases)}")

    engine = create_async_engine(get_database_url(), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    embedding = EmbeddingService()
    risk = RiskAnalysisService(retriever=SemanticRetriever(embedding))
    report = {"policy_version": POLICY_VERSION, "model": risk.model, "cases": []}

    try:
        for name, vendor_id in case_args:
            results = []
            for run in range(1, runs + 1):
                async with session_factory() as db:
                    analysis = await risk.analyze_vendor(db=db, vendor_id=vendor_id)
                overall, score = policy_outcome(analysis.findings)
                result = score_case(
                    cases[name],
                    findings=[
                        {
                            "category": f.category,
                            "finding_type": f.finding_type,
                            "title": f.title,
                            "description": f.description,
                        }
                        for f in analysis.findings
                    ],
                    coverage=[c.model_dump() for c in analysis.coverage],
                    overall_risk=overall,
                    summary=analysis.summary,
                )
                results.append(result)
                print(f"--- {name} run {run}/{runs} (score {score:.0f}/100)")
                print(format_result(result))
                print(flush=True)

            summary = summarize_runs(results)
            report["cases"].append(
                {"summary": summary, "runs": [r.to_dict() for r in results]}
            )
    finally:
        risk.close()
        await embedding.close()
        await engine.dispose()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", action="append", type=parse_case, required=True,
                        help="NAME=VENDOR_UUID; repeat for several cases")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--json-out", help="Also write the full report as JSON")
    args = parser.parse_args()
    if not 1 <= args.runs <= 10:
        parser.error("--runs must be between 1 and 10")

    report = asyncio.run(evaluate(args.case, args.runs, args.cases_file))

    print("================================")
    print("QUALITY EVALUATION SUMMARY")
    print("================================")
    print("Model:", report["model"], "| Policy:", report["policy_version"])
    all_passed = True
    injection_cases = {
        name for name, case in load_cases(args.cases_file).items() if case.canary_phrases
    }
    for case in report["cases"]:
        summary = case["summary"]
        all_passed &= summary["pass_rate"] == 1.0
        print(
            f"{summary['case']}: pass rate {summary['pass_rate']:.0%}, "
            f"mean recall {summary['mean_recall']:.0%}, "
            f"runs with false positives {summary['false_positive_runs']}/{summary['runs']}"
            + (
                f", runs obeying injected instructions "
                f"{summary['injection_runs']}/{summary['runs']}"
                if summary["case"] in injection_cases
                else ""
            )
        )
        for item, rate in summary["detection_rate"].items():
            print(f"  {rate:>5.0%}  {item}")

    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump(report, handle, indent=2, default=str)

    print("RESULT:", "PASS" if all_passed else "FAIL")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
