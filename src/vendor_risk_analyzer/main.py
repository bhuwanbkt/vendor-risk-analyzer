import asyncio
import os
import secrets
from contextlib import (
    asynccontextmanager,
    suppress,
)
from pathlib import Path

from fastapi import (
    FastAPI,
    Request,
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import (
    Jinja2Templates,
)
from starlette.middleware.sessions import (
    SessionMiddleware,
)

from vendor_risk_analyzer.api.documents import (
    router as documents_router,
)
from vendor_risk_analyzer.api.health import (
    router as health_router,
)
from vendor_risk_analyzer.api.vendors import (
    router as vendors_router,
)
from vendor_risk_analyzer.auth.routes import (
    router as auth_router,
)
from vendor_risk_analyzer.config import (
    get_settings,
)
from vendor_risk_analyzer.embeddings.worker import (
    run_embedding_worker,
)


BASE_DIR = Path(
    __file__
).resolve().parent

settings = get_settings()


def embedding_worker_enabled() -> bool:
    value = os.getenv(
        "EMBEDDING_WORKER_ENABLED",
        "true",
    )

    return (
        value
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    worker_task: (
        asyncio.Task | None
    ) = None

    if embedding_worker_enabled():
        worker_task = (
            asyncio.create_task(
                run_embedding_worker(),
                name="embedding-worker",
            )
        )

    try:
        yield

    finally:
        if worker_task is not None:
            worker_task.cancel()

            with suppress(
                asyncio.CancelledError
            ):
                await worker_task


app = FastAPI(
    title="Vendor Risk Analyzer",
    description=(
        "AI-powered vendor risk "
        "assessment platform"
    ),
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


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
    https_only=True,
)


# Register routes here
app.include_router(
    health_router
)

app.include_router(
    auth_router
)

app.include_router(
    vendors_router
)

app.include_router(
    documents_router
)


app.mount(
    "/static",
    StaticFiles(
        directory=(
            BASE_DIR / "static"
        )
    ),
    name="static",
)


templates = Jinja2Templates(
    directory=(
        BASE_DIR / "templates"
    ),
)


@app.get(
    "/",
    response_class=HTMLResponse,
)
async def dashboard(
    request: Request,
):
    user = request.session.get(
        "user"
    )

    csrf_token = (
        request.session.get(
            "csrf_token"
        )
    )

    if (
        user
        and not csrf_token
    ):
        csrf_token = (
            secrets.token_urlsafe(
                32
            )
        )

        request.session[
            "csrf_token"
        ] = csrf_token

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name":
                "Vendor Risk Analyzer",
            "user":
                user,
            "csrf_token":
                csrf_token,
        },
    )