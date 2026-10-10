"""Exercise the disposable Compose stack without ZITADEL/Gemini network calls.

Test identities use the development session secret, as the HTTP security tests do.
This script only accepts a localhost target and development configuration.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import dotenv_values
from itsdangerous import TimestampSigner


def main():
    config = dotenv_values(".env")
    assert config["APP_ENV"] == "development"
    app_url = f"http://localhost:{config.get('APP_PORT', '8000')}"
    assert urlsplit(app_url).hostname == "localhost"
    csrf = "compose-smoke-csrf"

    def cookie(role):
        payload = {
            "user": {"sub": "compose-smoke-user", "roles": [role]},
            "csrf_token": csrf,
        }
        data = base64.b64encode(json.dumps(payload).encode())
        signed = TimestampSigner(config["SESSION_SECRET"]).sign(data).decode()
        return "vendor_risk_session=" + signed

    def request(url, *, method="GET", data=None, headers=None):
        try:
            response = urlopen(
                Request(url, method=method, data=data, headers=headers or {}),
                timeout=20,
            )
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def api(path, *, method="GET", body=None, role="analyst"):
        headers = {"Cookie": cookie(role), "X-CSRF-Token": csrf}
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, _, result = request(
            app_url + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        return status, json.loads(result)

    assert request(app_url + "/health")[0] == 200
    assert request(app_url + "/ready")[0] == 200
    page = request(app_url + "/sign-in")
    assert page[0] == 200 and b"Sign in to your workspace" in page[2]
    assert request(app_url + "/api/vendors")[0] == 401
    status, _ = api(
        "/api/vendors",
        method="POST",
        body={"name": "Forbidden smoke vendor"},
        role="viewer",
    )
    assert status == 403
    status, vendor = api(
        "/api/vendors", method="POST", body={"name": "Compose smoke " + uuid4().hex}
    )
    assert status == 201
    prefix = f"/api/vendors/{vendor['id']}/documents"
    content = (
        b"Vendor Security Policy\n\nCustomer data is encrypted at rest using AES-256.\n"
    )
    status, upload = api(
        prefix + "/upload-url",
        method="POST",
        body={
            "filename": "compose-smoke.txt",
            "content_type": "text/plain",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert status == 200
    # Use the returned public URL exactly as a host browser does.
    status, headers, _ = request(
        upload["upload_url"],
        method="PUT",
        data=content,
        headers={"Content-Type": "text/plain", "Origin": app_url},
    )
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == app_url
    storage_url = (
        f"http://localhost:{config.get('MINIO_PORT', '9000')}/vendor-documents"
    )
    assert request(storage_url)[0] == 403
    document_url = prefix + "/" + upload["document_id"]
    status, document = api(document_url + "/complete", method="POST")
    assert status == 200 and document["status"] == "uploaded"
    status, document = api(document_url + "/ingest", method="POST")
    assert status == 200 and document["status"] == "embedding_pending"
    status, documents = api(prefix, role="viewer")
    assert status == 200 and len(documents) == 1
    assert documents[0]["id"] == upload["document_id"]
    print(
        "Compose smoke passed: migrations, HTTP sessions/RBAC, public signed upload, private bucket, internal download, parsing, and persistence."
    )


if __name__ == "__main__":
    main()
