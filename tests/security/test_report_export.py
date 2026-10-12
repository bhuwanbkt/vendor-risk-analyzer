"""Downloading assessment reports as PDF or Word."""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.security


def export(api, assessment_id, fmt):
    return api.client.get(f"/api/assessments/{assessment_id}/export?format={fmt}")


def test_viewer_downloads_word_report(security_api):
    from docx import Document

    security_api.authenticate(["viewer"])

    response = export(security_api, security_api.ids["assessment_a"], "docx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="vendor-a-risk-assessment')
    assert disposition.endswith('.docx"')
    assert response.headers["cache-control"] == "no-store"

    text = "\n".join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
    assert "Vendor A" in text
    assert "Private finding A" in text
    assert "Private finding B" not in text


def test_viewer_downloads_pdf_report(security_api):
    pymupdf = pytest.importorskip("pymupdf")
    security_api.authenticate(["viewer"])

    response = export(security_api, security_api.ids["assessment_a"], "pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert "Private finding A" in text


def test_export_requires_authentication_and_valid_format(security_api):
    assert export(security_api, security_api.ids["assessment_a"], "pdf").status_code == 401

    security_api.authenticate(["viewer"])
    assert export(security_api, security_api.ids["assessment_a"], "xlsx").status_code == 422


def test_unfinished_assessment_cannot_be_exported(security_api):
    security_api.authenticate(["analyst"])
    vendor = security_api.ids["vendor_a"]
    queued = security_api.client.post(
        f"/api/vendors/{vendor}/assessments",
        headers={"X-CSRF-Token": "test-csrf-token"},
    ).json()

    assert export(security_api, queued["id"], "pdf").status_code == 409
