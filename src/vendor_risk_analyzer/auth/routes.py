import httpx
from fastapi import APIRouter, HTTPException, Request
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
async def login(request: Request):
    return await oauth.zitadel.authorize_redirect(
        request,
        settings.zitadel_redirect_uri,
    )


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

    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Access token was not returned",
        )

    userinfo_url = (
        f"{settings.zitadel_issuer.rstrip('/')}"
        "/oidc/v1/userinfo"
    )

    async with httpx.AsyncClient(
        timeout=10.0,
    ) as client:
        response = await client.get(
            userinfo_url,
            headers={
                "Authorization": f"Bearer {access_token}"
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail="Unable to retrieve user information",
        )

    # User information returned by ZITADEL
    user = response.json()

    # Find this project's ZITADEL role claim
    role_claim = (
        "urn:zitadel:iam:org:project:"
        f"{settings.zitadel_project_id}:roles"
    )

    role_data = user.get(
        role_claim,
        {},
    )

    roles = list(
        role_data.keys()
    )

    # Create our application's session
    request.session["user"] = {
        "sub": user.get("sub"),
        "name": user.get("name"),
        "email": user.get("email"),
        "preferred_username": user.get(
            "preferred_username"
        ),
        "roles": roles,
    }

    return RedirectResponse(
        url="/",
        status_code=303,
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()

    logout_url = (
        f"{settings.zitadel_issuer.rstrip('/')}"
        "/oidc/v1/end_session"
        f"?post_logout_redirect_uri="
        f"{settings.zitadel_post_logout_uri}"
    )

    return RedirectResponse(
        url=logout_url,
        status_code=303,
    )


@router.get("/me")
async def current_user(request: Request):
    return get_current_user(request)
