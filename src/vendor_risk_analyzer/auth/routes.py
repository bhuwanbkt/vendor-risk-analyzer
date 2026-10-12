import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import RedirectResponse

from vendor_risk_analyzer.audit.service import record as audit
from vendor_risk_analyzer.auth.dependencies import get_current_user, verify_csrf
from vendor_risk_analyzer.auth.oidc import oauth
from vendor_risk_analyzer.auth.organizations import organization_roles, scoped_identity
from vendor_risk_analyzer.config import get_settings
from vendor_risk_analyzer.auth.access import resolve_access
from vendor_risk_analyzer.db.session import get_db

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

settings = get_settings()


@router.get("/login")
async def login(request: Request, login_hint: str = Query(default="", max_length=320)):
    # A new login must not retain the previous account while authentication runs.
    request.session.clear()
    authorization_params = {"prompt": "login"}
    if login_hint.strip():
        authorization_params["login_hint"] = login_hint.strip()

    response = await oauth.zitadel.authorize_redirect(
        request,
        settings.zitadel_redirect_uri,
        **authorization_params,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/callback")
async def callback(request: Request):
    try:
        token = await oauth.zitadel.authorize_access_token(
            request,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
        ) from exc

    access_token = token.get("access_token")

    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=401,
            detail="Access token was not returned",
        )

    # Authlib adds these claims only after validating the ID token and nonce.
    id_user = token.get("userinfo")
    subject = id_user.get("sub") if isinstance(id_user, dict) else None
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
        )

    userinfo_url = (
        f"{settings.zitadel_issuer.rstrip('/')}"
        "/oidc/v1/userinfo"
    )

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
        ) as client:
            response = await client.get(
                userinfo_url,
                headers={
                    "Authorization": f"Bearer {access_token}"
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=401,
            detail="Unable to retrieve user information",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail="Unable to retrieve user information",
        )

    try:
        user = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail="Unable to retrieve user information",
        ) from exc

    # OIDC requires UserInfo to identify the same subject as the validated ID token.
    if not isinstance(user, dict) or user.get("sub") != subject:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
        )

    # Find this project's ZITADEL role claim
    role_claim = (
        "urn:zitadel:iam:org:project:"
        f"{settings.zitadel_project_id}:roles"
    )

    role_data = user.get(
        role_claim,
        {},
    )

    grants = organization_roles(role_data)

    # Replace temporary OIDC state and any prior account's CSRF token.
    request.session.clear()
    request.session["user"] = scoped_identity({
        "sub": user.get("sub"),
        "name": user.get("name"),
        "email": user.get("email"),
        "preferred_username": user.get(
            "preferred_username"
        ),
        "organization_roles": grants,
    })
    request.session["csrf_token"] = secrets.token_urlsafe(32)

    return RedirectResponse(
        url="/",
        status_code=303,
    )


@router.get("/logout")
async def logout(request: Request):
    user = request.session.get("user")
    request.session.clear()

    params = {
        "client_id": settings.zitadel_client_id,
        "post_logout_redirect_uri": settings.zitadel_post_logout_uri,
    }
    # Login UI V2 can select the account to sign out using its verified login name.
    if isinstance(user, dict):
        login_name = user.get("preferred_username")
        if isinstance(login_name, str) and 0 < len(login_name.strip()) <= 320:
            params["logout_hint"] = login_name.strip()

    logout_params = urlencode(params)
    logout_url = (
        f"{settings.zitadel_issuer.rstrip('/')}"
        "/oidc/v1/end_session"
        f"?{logout_params}"
    )

    return RedirectResponse(
        url=logout_url,
        status_code=303,
    )


@router.get("/me")
async def current_user(request: Request):
    return get_current_user(request)


class CompanySelection(BaseModel):
    organization_id: str = Field(min_length=1, max_length=255)


@router.post("/company")
async def select_company(payload: CompanySelection, request: Request,
                         db=Depends(get_db), _=Depends(verify_csrf)):
    # Company switching belongs to browser sessions; tokens choose per request.
    if request.headers.get("Authorization"):
        raise HTTPException(400, "Use X-Organization-ID with bearer requests")
    user = get_current_user(request)
    user = scoped_identity(user, payload.organization_id)
    access = await resolve_access(db, user)
    await audit(
        access,
        user,
        "company.selected",
        target_type="company",
        target_id=access.tenant.id,
    )
    request.session["user"] = user
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    return {"redirect": "/dashboard"}
