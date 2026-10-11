"""Bind existing data or register an empty company using an explicit org ID."""

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vendor_risk_analyzer.auth.tenant_registration import register_tenant
from vendor_risk_analyzer.db.url import get_database_url


async def run(args):
    load_dotenv()
    engine = create_async_engine(get_database_url())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            tenant = await register_tenant(
                db,
                organization_id=args.org_id,
                name=args.name,
                legacy=args.legacy,
                default_access=args.default_access,
                write=args.write,
            )
            print(
                "Company registered"
                if args.write
                else "Preview only; add --write to apply"
            )
            print(
                "Existing workspace" if args.legacy else "Company workspace",
                tenant.id or "new",
            )
            print("ZITADEL Organization ID:", args.org_id)
            print("Company name:", args.name)
            print(
                "Default vendor access:", args.default_access or tenant.default_access
            )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Bind the existing workspace; never rebind it",
    )
    parser.add_argument("--default-access", choices=["all", "assigned"])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("ERROR: Company registration database operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
