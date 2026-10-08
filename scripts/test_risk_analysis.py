from __future__ import annotations

import os
import sys
from typing import Literal

from google import genai
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
)


MODEL = os.getenv(
    "RISK_LLM_MODEL",
    "gemini-3.8-flash",
)


class RiskSignal(BaseModel):
    finding_type: Literal[
        "contradiction",
        "evidence_gap",
        "explicit_risk",
    ]

    category: Literal[
        "access_control",
        "encryption",
        "incident_response",
        "data_retention",
        "vulnerability_management",
        "business_continuity",
        "third_party",
        "privacy",
        "ai_governance",
        "other",
    ]

    title: str = Field(
        min_length=5,
        max_length=200,
    )

    description: str = Field(
        min_length=10,
        max_length=1000,
    )

    evidence_ids: list[str] = Field(
        min_length=1,
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )


class EvidenceAnalysis(BaseModel):
    summary: str = Field(
        min_length=10,
        max_length=1000,
    )

    findings: list[RiskSignal]


def build_prompt() -> str:
    return """
You are analyzing vendor security evidence.

Your job is NOT to invent compliance requirements
or decide whether a vendor meets an unstated policy.

Only identify:

1. Contradictions between supplied evidence.
2. Explicitly documented control or evidence gaps.
3. Explicit risks stated directly in the evidence.

Rules:

- Use only the evidence below.
- Never invent facts.
- Never create evidence IDs.
- Every finding must cite one or more supplied evidence IDs.
- Do not treat strong controls as risks.
- Do not declare something noncompliant unless the evidence
  itself establishes that.
- Missing information is not automatically a risk.
- An explicit statement that required evidence is missing
  may be reported as an evidence_gap.
- Conflicting statements should be reported as a contradiction.
- Do not calculate an overall risk score.
- Do not invent regulatory thresholds.

EVIDENCE:

[E1]
category: incident_response
source: Current Security Policy
text:
Security incidents affecting customer data will be
reported to customers within 48 hours of confirmation.

[E2]
category: incident_response
source: Previous Vendor Assessment
text:
Customers will be notified of confirmed security
incidents within 72 hours.

[E3]
category: business_continuity
source: Risk Register
text:
Disaster recovery exercise evidence has not yet been
provided for the current review period.

[E4]
category: encryption
source: Security Architecture
text:
Customer data is encrypted using AES-256 at rest and
TLS 1.3 while in transit.

Analyze the evidence.
""".strip()


def validate_evidence_ids(
    analysis: EvidenceAnalysis,
) -> None:
    allowed_ids = {
        "E1",
        "E2",
        "E3",
        "E4",
    }

    for finding in analysis.findings:
        for evidence_id in finding.evidence_ids:
            if evidence_id not in allowed_ids:
                raise ValueError(
                    "Model returned an invalid "
                    f"evidence ID: {evidence_id}"
                )


def main() -> int:
    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:
        print(
            "FAIL: GEMINI_API_KEY "
            "is not configured."
        )

        return 1

    print(
        "Testing structured "
        "vendor-risk analysis..."
    )

    print(
        f"Model: {MODEL}"
    )

    client = genai.Client(
        api_key=api_key
    )

    try:
        interaction = (
            client.interactions.create(
                model=MODEL,
                input=build_prompt(),
                response_format={
                    "type": "text",
                    "mime_type":
                        "application/json",
                    "schema":
                        EvidenceAnalysis
                        .model_json_schema(),
                },
            )
        )

        if not interaction.output_text:
            print(
                "FAIL: Model returned "
                "no output."
            )

            return 1

        try:
            analysis = (
                EvidenceAnalysis
                .model_validate_json(
                    interaction.output_text
                )
            )

        except ValidationError as exc:
            print(
                "FAIL: Structured output "
                "validation failed."
            )

            print(exc)

            return 1

        validate_evidence_ids(
            analysis
        )

        print()
        print(
            "PASS: Received valid "
            "structured risk analysis."
        )

        print()
        print(
            "Summary:"
        )
        print(
            analysis.summary
        )

        print()
        print(
            f"Findings: "
            f"{len(analysis.findings)}"
        )

        for index, finding in enumerate(
            analysis.findings,
            start=1,
        ):
            print()
            print(
                f"Finding {index}"
            )

            print(
                "  Type:",
                finding.finding_type,
            )

            print(
                "  Category:",
                finding.category,
            )

            print(
                "  Title:",
                finding.title,
            )

            print(
                "  Evidence:",
                ", ".join(
                    finding.evidence_ids
                ),
            )

            print(
                "  Confidence:",
                finding.confidence,
            )

            print(
                "  Description:",
                finding.description,
            )

        print()
        print(
            "Structured analysis "
            "test completed successfully."
        )

        return 0

    except Exception as exc:
        print(
            "FAIL: Gemini risk analysis "
            "request failed."
        )

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return 1

    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(
        main()
    )