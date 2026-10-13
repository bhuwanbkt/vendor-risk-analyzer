"""Tell PostgreSQL which company a transaction belongs to (row-level security).

Every transaction on a session marked here starts with

    SELECT set_config('app.tenant_id', <company id>, true),
           set_config('app.rls_bypass', 'on' | 'off', true)

and the policies from migration e3b7d1f5a9c4 then hide other companies'
rows, even from a query that forgot its company filter. The settings are
transaction-local (``true``), so pooled connections never carry them over.

- Requests call ``bind_tenant`` as soon as the company is known
  (``resolve_access``), before reading company data.
- Background workers process queued work for every company. Their engines
  use ``enable_system_access``; an assessment job then narrows its analysis
  session to the vendor's company with ``bind_tenant``.

Sessions that are never marked leave the settings empty, which matches no
company: a forgotten ``bind_tenant`` returns no rows instead of everyone's.
On SQLite (tests) nothing is executed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.orm import Session

TENANT_KEY = "rls_tenant_id"
BYPASS_KEY = "rls_bypass"

SET_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), "
    "set_config('app.rls_bypass', :bypass, true)"
)


def _sync_session(db) -> Session | None:
    """The ORM Session behind an AsyncSession (or a test wrapper).

    Returns None for test doubles that are not backed by a database.
    """
    if isinstance(db, Session):
        return db
    for attribute in ("sync_session", "session"):
        inner = getattr(db, attribute, None)
        if isinstance(inner, Session):
            return inner
    return None


def _is_postgresql(session: Session) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _parameters(info: dict) -> dict[str, str]:
    tenant = info.get(TENANT_KEY)
    return {
        "tenant_id": str(tenant) if tenant else "",
        "bypass": "on" if info.get(BYPASS_KEY) else "off",
    }


async def _apply_now(db, session: Session) -> None:
    # The current transaction has already begun; set it immediately.
    # Later transactions are covered by the after_begin listener.
    if session.in_transaction() and _is_postgresql(session):
        await db.execute(SET_CONTEXT, _parameters(session.info))


async def bind_tenant(db, tenant_id: UUID | str) -> None:
    """Limit this session's transactions to one company."""
    session = _sync_session(db)
    if session is None:
        return
    session.info[TENANT_KEY] = str(tenant_id)
    session.info[BYPASS_KEY] = False
    await _apply_now(db, session)


async def bind_system(db) -> None:
    """Let this session see every company (trusted background work only)."""
    session = _sync_session(db)
    if session is None:
        return
    session.info.pop(TENANT_KEY, None)
    session.info[BYPASS_KEY] = True
    await _apply_now(db, session)


def bound_tenant(db) -> str | None:
    session = _sync_session(db)
    return session.info.get(TENANT_KEY) if session is not None else None


@event.listens_for(Session, "after_begin")
def _apply_on_begin(session, transaction, connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    if TENANT_KEY not in session.info and BYPASS_KEY not in session.info:
        return
    connection.execute(SET_CONTEXT, _parameters(session.info))


def enable_system_access(engine) -> None:
    """Every transaction on this engine may see all companies.

    For worker and operations engines. A session from such an engine can
    still be narrowed with ``bind_tenant`` (its after_begin runs later).
    """
    sync_engine = getattr(engine, "sync_engine", engine)
    dialect = getattr(getattr(sync_engine, "dialect", None), "name", None)
    if dialect != "postgresql":
        return

    @event.listens_for(sync_engine, "begin")
    def _system_begin(connection) -> None:
        connection.exec_driver_sql(
            "SELECT set_config('app.rls_bypass', 'on', true)"
        )


async def rls_status(db) -> dict:
    """Whether the current database role is actually subject to the policies."""
    session = _sync_session(db)
    if session is None or not _is_postgresql(session):
        return {"enforced": False, "role": None, "reason": "Not PostgreSQL"}
    row = (
        await db.execute(
            text(
                """
                SELECT current_user,
                       r.rolsuper,
                       r.rolbypassrls,
                       c.relowner = r.oid AS owns_tables,
                       c.relrowsecurity
                FROM pg_roles r, pg_class c
                WHERE r.rolname = current_user
                  AND c.oid = to_regclass('vendors')
                """
            )
        )
    ).first()
    if row is None:
        return {"enforced": False, "role": None, "reason": "Tables not found"}
    role, superuser, bypassrls, owns_tables, policies_on = row
    reason = None
    if not policies_on:
        reason = "The row-level security migration has not run."
    elif superuser:
        reason = f"Role {role} is a superuser."
    elif bypassrls:
        reason = f"Role {role} has BYPASSRLS."
    elif owns_tables:
        reason = f"Role {role} owns the tables."
    return {"enforced": reason is None, "role": role, "reason": reason}
