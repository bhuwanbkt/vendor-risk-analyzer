from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_application_starts_outside_repository_without_cloud_calls(tmp_path) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://test:test@127.0.0.1:1/test",
        "ZITADEL_ISSUER": "https://identity.example.invalid",
        "ZITADEL_CLIENT_ID": "test-client",
        "ZITADEL_PROJECT_ID": "test-project",
        "ZITADEL_REDIRECT_URI": "https://testserver/auth/callback",
        "ZITADEL_POST_LOGOUT_URI": "https://testserver/",
        "SESSION_SECRET": "test-session-secret",
        "OBJECT_STORAGE_ENDPOINT": "https://storage.example.invalid",
        "OBJECT_STORAGE_REGION": "test-region",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "test-access-key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "test-secret-key",
        "EMBEDDING_WORKER_ENABLED": "false",
        "GEMINI_API_KEY": "",
    }
    script = textwrap.dedent("""
        import socket

        def reject_network(*args, **kwargs):
            raise AssertionError("Startup smoke test attempted a network call")

        socket.socket.connect = reject_network
        socket.socket.connect_ex = reject_network

        from fastapi.testclient import TestClient
        from vendor_risk_analyzer.main import app

        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/auth/login"

            assert client.get("/api/vendors").status_code == 401
            assert client.get("/static/css/app.css").status_code == 200
    """)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
