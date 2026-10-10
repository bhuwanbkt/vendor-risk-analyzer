import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from vendor_risk_analyzer.auth.dependencies import get_current_user
from vendor_risk_analyzer.auth.oidc import oauth
from vendor_risk_analyzer.config import get_settings

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

    roles = list(role_data) if isinstance(role_data, dict) else []

    # Replace temporary OIDC state and any prior account's CSRF token.
    request.session.clear()
    request.session["user"] = {
        "sub": user.get("sub"),
        "name": user.get("name"),
        "email": user.get("email"),
        "preferred_username": user.get(
            "preferred_username"
        ),
        "roles": roles,
    }
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
