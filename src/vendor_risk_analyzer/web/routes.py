from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)

from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
)

from fastapi.templating import (
    Jinja2Templates,
)

from sqlalchemy import (
    func,
    select,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from vendor_risk_analyzer.auth.dependencies import get_current_user, get_user_roles

from vendor_risk_analyzer.db.models import (
    Assessment,
    Finding,
    Vendor,
)

from vendor_risk_analyzer.db.session import (
    get_db,
)

from vendor_risk_analyzer.web.navigation import (
    build_navigation,
)


BASE_DIR = (
    Path(__file__)
    .resolve()
    .parents[1]
)

templates = Jinja2Templates(
    directory=(
        BASE_DIR
        / "templates"
    )
)

router = APIRouter(
    tags=["Web"],
)


# ============================================================
# USER / PAGE SECURITY
# ============================================================


def get_session_user(
    request: Request,
) -> dict | None:
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def require_page_roles(
    user: dict,
    *allowed_roles: str,
) -> None:
    roles = get_user_roles(
        user
    )

    if not any(
        role in roles
        for role in allowed_roles
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "You do not have permission "
                "to access this page."
            ),
        )


def ensure_csrf_token(
    request: Request,
) -> str:
    csrf_token = request.session.get(
        "csrf_token"
    )

    if not isinstance(csrf_token, str) or not csrf_token or not csrf_token.isascii():
        csrf_token = (
            secrets.token_urlsafe(
                32
            )
        )

        request.session[
            "csrf_token"
        ] = csrf_token

    return csrf_token


def user_display_name(
    user: dict,
) -> str:
    return str(
        user.get("name")
        or user.get("email")
        or user.get(
            "preferred_username"
        )
        or "User"
    )


def user_initials(
    user: dict,
) -> str:
    name = user_display_name(
        user
    ).strip()

    parts = [
        part
        for part in name.split()
        if part
    ]

    if not parts:
        return "U"

    if len(parts) == 1:
        return (
            parts[0][:2]
            .upper()
        )

    return (
        parts[0][0]
        + parts[-1][0]
    ).upper()


def page_context(
    *,
    request: Request,
    user: dict,
    active_page: str,
    page_title: str,
    **extra,
) -> dict:

    roles = get_user_roles(
        user
    )

    context = {
        "request": request,
        "user": user,
        "roles": roles,
        "display_name": (
            user_display_name(
                user
            )
        ),
        "initials": (
            user_initials(
                user
            )
        ),
        "navigation": (
            build_navigation(
                user
            )
        ),
        "active_page": (
            active_page
        ),
        "page_title": (
            page_title
        ),
        "csrf_token": (
            ensure_csrf_token(
                request
            )
        ),
        "can_manage": (
            "analyst" in roles
            or "admin" in roles
        ),
        **extra,
    }

    return context


def login_redirect():
    return RedirectResponse(
        url="/auth/login",
        status_code=302,
    )


# ============================================================
# ROOT
# ============================================================


@router.get("/")
async def root(
    request: Request,
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    return RedirectResponse(
        url="/dashboard",
        status_code=302,
    )


# ============================================================
# DASHBOARD
# ============================================================


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
)
async def dashboard_page(
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    require_page_roles(user, "viewer", "analyst", "admin")

    vendor_count = (
        await db.scalar(
            select(
                func.count(
                    Vendor.id
                )
            )
        )
        or 0
    )


    assessment_count = (
        await db.scalar(
            select(
                func.count(
                    Assessment.id
                )
            )
            .where(
                Assessment.status
                == "completed"
            )
        )
        or 0
    )


    finding_count = (
        await db.scalar(
            select(
                func.count(
                    Finding.id
                )
            )
            .where(
                Finding.status
                == "open"
            )
        )
        or 0
    )


    recent_result = await db.execute(
        select(
            Assessment,
            Vendor.name,
        )
        .join(
            Vendor,
            Vendor.id
            == Assessment.vendor_id,
        )
        .order_by(
            Assessment.created_at.desc()
        )
        .limit(6)
    )

    recent_assessments = [
        {
            "assessment": assessment,
            "vendor_name": vendor_name,
        }
        for (
            assessment,
            vendor_name,
        )
        in recent_result.all()
    ]


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/dashboard.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page="dashboard",
            page_title="Dashboard",
            vendor_count=(
                vendor_count
            ),
            assessment_count=(
                assessment_count
            ),
            finding_count=(
                finding_count
            ),
            recent_assessments=(
                recent_assessments
            ),
        ),
    )


# ============================================================
# VENDORS
# ============================================================


@router.get(
    "/vendors",
    response_class=HTMLResponse,
)
async def vendors_page(
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    require_page_roles(user, "viewer", "analyst", "admin")

    result = await db.execute(
        select(
            Vendor
        )
        .order_by(
            Vendor.name
        )
    )

    vendors = list(
        result.scalars().all()
    )


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/vendors.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page="vendors",
            page_title="Vendors",
            vendors=vendors,
        ),
    )


# ============================================================
# DOCUMENTS
# ============================================================


@router.get(
    "/documents",
    response_class=HTMLResponse,
)
async def documents_page(
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    require_page_roles(user, "viewer", "analyst", "admin")

    result = await db.execute(
        select(
            Vendor
        )
        .order_by(
            Vendor.name
        )
    )

    vendors = list(
        result.scalars().all()
    )


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/documents.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page="documents",
            page_title="Documents",
            vendors=vendors,
        ),
    )


# ============================================================
# ASSESSMENTS
# ============================================================


@router.get(
    "/assessments",
    response_class=HTMLResponse,
)
async def assessments_page(
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    require_page_roles(user, "viewer", "analyst", "admin")

    vendor_result = await db.execute(
        select(
            Vendor
        )
        .order_by(
            Vendor.name
        )
    )

    vendors = list(
        vendor_result
        .scalars()
        .all()
    )


    assessment_result = (
        await db.execute(
            select(
                Assessment,
                Vendor.name,
            )
            .join(
                Vendor,
                Vendor.id
                == Assessment.vendor_id,
            )
            .order_by(
                Assessment.created_at
                .desc()
            )
            .limit(50)
        )
    )


    assessment_rows = (
        assessment_result.all()
    )


    assessment_ids = [
        assessment.id
        for assessment, _
        in assessment_rows
    ]


    finding_counts: dict[
        UUID,
        int,
    ] = {}


    if assessment_ids:
        finding_result = (
            await db.execute(
                select(
                    Finding.assessment_id,
                    func.count(
                        Finding.id
                    ),
                )
                .where(
                    Finding.assessment_id
                    .in_(
                        assessment_ids
                    )
                )
                .group_by(
                    Finding.assessment_id
                )
            )
        )

        finding_counts = {
            assessment_id: int(
                count
            )
            for (
                assessment_id,
                count,
            )
            in finding_result.all()
        }


    assessments = [
        {
            "assessment": (
                assessment
            ),
            "vendor_name": (
                vendor_name
            ),
            "finding_count": (
                finding_counts.get(
                    assessment.id,
                    0,
                )
            ),
        }
        for (
            assessment,
            vendor_name,
        )
        in assessment_rows
    ]


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/assessments.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page=(
                "assessments"
            ),
            page_title=(
                "Assessments"
            ),
            vendors=vendors,
            assessments=assessments,
        ),
    )


# ============================================================
# ASSESSMENT DETAIL
# ============================================================


@router.get(
    "/assessments/{assessment_id}",
    response_class=HTMLResponse,
)
async def assessment_detail_page(
    assessment_id: UUID,
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()

    require_page_roles(user, "viewer", "analyst", "admin")

    assessment = await db.get(
        Assessment,
        assessment_id,
    )

    if assessment is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Assessment not found."
            ),
        )


    vendor = await db.get(
        Vendor,
        assessment.vendor_id,
    )


    finding_result = (
        await db.execute(
            select(
                Finding
            )
            .where(
                Finding.assessment_id
                == assessment.id
            )
            .order_by(
                Finding.created_at
            )
        )
    )


    findings = list(
        finding_result
        .scalars()
        .all()
    )


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/"
            "assessment_detail.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page=(
                "assessments"
            ),
            page_title=(
                "Assessment Report"
            ),
            assessment=assessment,
            vendor=vendor,
            findings=findings,
        ),
    )


# ============================================================
# CHAT
# ============================================================


@router.get(
    "/chat",
    response_class=HTMLResponse,
)
async def chat_page(
    request: Request,
    db: AsyncSession = Depends(
        get_db
    ),
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()


    require_page_roles(
        user,
        "analyst",
        "admin",
    )


    result = await db.execute(
        select(
            Vendor
        )
        .order_by(
            Vendor.name
        )
    )

    vendors = list(
        result.scalars().all()
    )


    return templates.TemplateResponse(
        request=request,
        name="pages/chat.html",
        context=page_context(
            request=request,
            user=user,
            active_page="chat",
            page_title=(
                "AI Risk Assistant"
            ),
            vendors=vendors,
        ),
    )


# ============================================================
# PROFILE
# ============================================================


@router.get(
    "/profile",
    response_class=HTMLResponse,
)
async def profile_page(
    request: Request,
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/profile.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page="profile",
            page_title="My Profile",
        ),
    )


# ============================================================
# ADMIN SYSTEM
# ============================================================


@router.get(
    "/admin/system",
    response_class=HTMLResponse,
)
async def system_page(
    request: Request,
):
    user = get_session_user(
        request
    )

    if user is None:
        return login_redirect()


    require_page_roles(
        user,
        "admin",
    )


    system_info = {
        "embedding_model": (
            os.getenv(
                "EMBEDDING_MODEL",
                "Not configured",
            )
        ),
        "embedding_dimensions": (
            os.getenv(
                "EMBEDDING_DIMENSIONS",
                "Not configured",
            )
        ),
        "risk_model": (
            os.getenv(
                "RISK_LLM_MODEL",
                "Not configured",
            )
        ),
        "chat_model": (
            os.getenv(
                "CHAT_LLM_MODEL",
                os.getenv(
                    "RISK_LLM_MODEL",
                    "Not configured",
                ),
            )
        ),
    }


    return templates.TemplateResponse(
        request=request,
        name=(
            "pages/system.html"
        ),
        context=page_context(
            request=request,
            user=user,
            active_page="system",
            page_title="System",
            system_info=system_info,
        ),
    )
