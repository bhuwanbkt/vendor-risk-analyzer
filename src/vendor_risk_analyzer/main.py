from __future__ import annotations

import asyncio

from contextlib import (
    AsyncExitStack,
    asynccontextmanager,
    suppress,
)

from pathlib import Path

from fastapi import (
    FastAPI,
)

from fastapi.staticfiles import (
    StaticFiles,
)

from starlette.middleware.sessions import (
    SessionMiddleware,
)


# ============================================================
# API ROUTERS
# ============================================================

from vendor_risk_analyzer.api.health import (
    router as health_router,
)

from vendor_risk_analyzer.api.vendors import (
    router as vendors_router,
)

from vendor_risk_analyzer.api.documents import (
    router as documents_router,
)

from vendor_risk_analyzer.api.assessments import (
    router as assessments_router,
)

from vendor_risk_analyzer.api.chat import (
    router as chat_router,
)


# ============================================================
# AUTH
# ============================================================

from vendor_risk_analyzer.auth.routes import (
    router as auth_router,
)
from vendor_risk_analyzer.auth.bearer import ApiBearerAuthenticationMiddleware


# ============================================================
# SETTINGS
# ============================================================

from vendor_risk_analyzer.config import (
    get_settings,
)


# ============================================================
# AUTOMATIC EMBEDDING WORKER
# ============================================================

from vendor_risk_analyzer.embeddings.worker import (
    run_embedding_worker,
)


# ============================================================
# WEB APPLICATION ROUTES
# ============================================================

from vendor_risk_analyzer.web.routes import (
    router as web_router,
)


# ============================================================
# BASE DIRECTORY / SETTINGS
# ============================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
)

settings = get_settings()

mcp_application = None
if settings.mcp_enabled:
    from vendor_risk_analyzer.mcp.server import create_mcp_application

    mcp_application = create_mcp_application(settings)


# ============================================================
# APPLICATION LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run MCP transport and the automatic embedding worker in this process."""
    async with AsyncExitStack() as stack:
        if mcp_application is not None:
            await stack.enter_async_context(
                mcp_application.app.router.lifespan_context(mcp_application.app)
            )
        embedding_worker_task = asyncio.create_task(
            run_embedding_worker(), name="automatic-embedding-worker"
        )
        try:
            yield
        finally:
            if not embedding_worker_task.done():
                embedding_worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await embedding_worker_task


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=(
        "Vendor Risk Analyzer"
    ),

    description=(
        "AI-powered vendor security "
        "and compliance assessment platform"
    ),

    version="0.2.0",

    # --------------------------------------------------------
    # Public API documentation remains disabled.
    # --------------------------------------------------------

    docs_url=None,

    redoc_url=None,

    openapi_url=None,

    # --------------------------------------------------------
    # Startup / shutdown lifecycle.
    # --------------------------------------------------------

    lifespan=lifespan,
)


# ============================================================
# SESSION MIDDLEWARE
# ============================================================

app.add_middleware(
    SessionMiddleware,

    secret_key=(
        settings.session_secret
    ),

    session_cookie=(
        "vendor_risk_session"
    ),

    max_age=3600,

    same_site="lax",

    https_only=settings.session_cookie_secure,
)

app.add_middleware(ApiBearerAuthenticationMiddleware)


# ============================================================
# HEALTH / READINESS ROUTES
# ============================================================

app.include_router(
    health_router
)


# ============================================================
# AUTHENTICATION ROUTES
# ============================================================

app.include_router(
    auth_router
)


# ============================================================
# VENDOR API
# ============================================================

app.include_router(
    vendors_router
)


# ============================================================
# DOCUMENT API
# ============================================================

app.include_router(
    documents_router
)


# ============================================================
# ASSESSMENT API
# ============================================================

app.include_router(
    assessments_router
)


# ============================================================
# GROUNDED CHAT API
# ============================================================

app.include_router(
    chat_router
)


# ============================================================
# WEB APPLICATION ROUTES
# ============================================================

# Web routes are defined in:
#
# vendor_risk_analyzer.web.routes
#
# Pages:
#
# /
# /dashboard
# /vendors
# /documents
# /assessments
# /assessments/{assessment_id}
# /chat
# /profile
# /admin/system
#
# Page visibility and authorization
# are handled in the web routing layer.

app.include_router(
    web_router
)


# ============================================================
# STATIC FILES
# ============================================================

# Serves:
#
# /static/css/app.css
#
# /static/js/common.js
# /static/js/vendors.js
# /static/js/documents.js
# /static/js/assessments.js
# /static/js/chat.js

app.mount(
    "/static",

    StaticFiles(
        directory=(
            BASE_DIR
            / "static"
        ),
    ),

    name="static",
)

# Root mounting preserves the SDK's /mcp and RFC 9728 discovery paths.
# Existing API/web/static routes are matched before the MCP app.
if mcp_application is not None:
    app.mount("/", mcp_application.app, name="mcp")
