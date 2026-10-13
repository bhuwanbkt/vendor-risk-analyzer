"""Row-level security context: which company a database transaction is for.

The policies themselves are verified against PostgreSQL by tests/rls_smoke.py
in the Compose CI job; these tests cover the application side.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from vendor_risk_analyzer.db import tenant_context as tc


pytestmark = pytest.mark.unit

TENANT = UUID(int=42)


class Recorder:
    def __init__(self, dialect="postgresql"):
        self.dialect = SimpleNamespace(name=dialect)
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))

    def exec_driver_sql(self, statement):
        self.statements.append((statement, None))


def sqlite_session() -> Session:
    return Session(create_engine("sqlite://"))


def test_bind_tenant_marks_the_session_without_sql_on_sqlite() -> None:
    session = sqlite_session()
    session.begin()

    asyncio.run(tc.bind_tenant(SimpleNamespace(session=session), TENANT))

    assert session.info[tc.TENANT_KEY] == str(TENANT)
    assert session.info[tc.BYPASS_KEY] is False
    assert tc.bound_tenant(session) == str(TENANT)


def test_bind_system_clears_the_company_and_bypasses() -> None:
    session = sqlite_session()
    session.info[tc.TENANT_KEY] = str(TENANT)

    asyncio.run(tc.bind_system(session))

    assert tc.TENANT_KEY not in session.info
    assert session.info[tc.BYPASS_KEY] is True


def test_test_doubles_are_ignored() -> None:
    fake = SimpleNamespace(add=lambda value: None)

    asyncio.run(tc.bind_tenant(fake, TENANT))
    asyncio.run(tc.bind_system(fake))

    assert tc.bound_tenant(fake) is None


def test_each_postgresql_transaction_starts_with_the_company() -> None:
    session = sqlite_session()
    session.info[tc.TENANT_KEY] = str(TENANT)
    session.info[tc.BYPASS_KEY] = False
    connection = Recorder()

    tc._apply_on_begin(session, None, connection)

    ((statement, parameters),) = connection.statements
    assert "set_config('app.tenant_id'" in statement
    assert "set_config('app.rls_bypass'" in statement
    # Transaction-local, so pooled connections never keep it.
    assert statement.count(", true)") == 2
    assert parameters == {"tenant_id": str(TENANT), "bypass": "off"}


def test_system_sessions_bypass_and_unmarked_sessions_are_left_alone() -> None:
    session = sqlite_session()
    connection = Recorder()

    tc._apply_on_begin(session, None, connection)
    assert connection.statements == []

    session.info[tc.BYPASS_KEY] = True
    tc._apply_on_begin(session, None, connection)
    assert connection.statements[0][1] == {"tenant_id": "", "bypass": "on"}

    sqlite_connection = Recorder(dialect="sqlite")
    tc._apply_on_begin(session, None, sqlite_connection)
    assert sqlite_connection.statements == []


def test_system_engines_bypass_in_every_transaction() -> None:
    engine = create_async_engine("postgresql+asyncpg://user:pass@db.invalid/app")
    tc.enable_system_access(engine)
    listeners = list(engine.sync_engine.dispatch.begin)
    connection = Recorder()

    for listener in listeners:
        listener(connection)

    assert connection.statements == [
        ("SELECT set_config('app.rls_bypass', 'on', true)", None)
    ]


def test_system_access_is_a_no_op_off_postgresql() -> None:
    engine = create_engine("sqlite://")

    tc.enable_system_access(engine)
    tc.enable_system_access(SimpleNamespace(dispose=None))  # worker test double

    assert list(engine.dispatch.begin) == []


def test_rls_status_off_postgresql() -> None:
    status = asyncio.run(tc.rls_status(sqlite_session()))

    assert status == {"enforced": False, "role": None, "reason": "Not PostgreSQL"}


def test_assessment_job_narrows_to_the_vendors_company(monkeypatch) -> None:
    from vendor_risk_analyzer.assessments import worker

    bind = AsyncMock()
    monkeypatch.setattr(worker, "bind_tenant", bind)
    db = SimpleNamespace(scalar=AsyncMock(return_value=TENANT))

    asyncio.run(worker.bind_vendor_company(db, UUID(int=7)))

    bind.assert_awaited_once_with(db, TENANT)


def test_migration_covers_every_company_owned_table() -> None:
    import importlib.util
    from pathlib import Path

    from vendor_risk_analyzer.db.base import Base
    import vendor_risk_analyzer.db.models  # noqa: F401

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/e3b7d1f5a9c4_add_row_level_security.py"
    )
    spec = importlib.util.spec_from_file_location("rls_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    # Tables deliberately left without policies.
    unprotected = {"tenants", "rate_limit_buckets"}
    assert set(migration.OWNERSHIP) == set(Base.metadata.tables) - unprotected
    for table, condition in migration.OWNERSHIP.items():
        statements = migration.policy_statements(table, condition)
        assert "app_rls_bypass() OR" in statements[-1]
        assert "USING" in statements[-1] and "WITH CHECK" in statements[-1]
    # asyncpg prepares each statement: exactly one command per execute.
    for statement in migration.FUNCTIONS + [
        s
        for table, condition in migration.OWNERSHIP.items()
        for s in migration.policy_statements(table, condition)
    ]:
        assert ";" not in statement, statement
