from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from vendor_risk_analyzer.api.health import router as health_router

from starlette.middleware.sessions import SessionMiddleware

from vendor_risk_analyzer.auth.routes import router as auth_router
from vendor_risk_analyzer.config import get_settings

from vendor_risk_analyzer.api.vendors import (
    router as vendors_router,
)




BASE_DIR = Path(__file__).resolve().parent

settings = get_settings()

app = FastAPI(
    title="Vendor Risk Analyzer",
    description="AI-powered vendor risk assessment platform",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="vendor_risk_session",
    max_age=3600,
    same_site="lax",
    https_only=True,
)

# Register routes here
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(vendors_router)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(
    directory=BASE_DIR / "templates",
)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = request.session.get("user")

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": "Vendor Risk Analyzer",
            "user": user,
        },
    )