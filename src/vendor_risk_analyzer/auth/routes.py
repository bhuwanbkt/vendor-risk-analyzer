from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

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

    user = token.get("userinfo")

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User information was not returned",
        )

    # Store only the minimum identity information.
    # Do NOT store access/ID tokens in the cookie.
    request.session["user"] = {
        "sub": user.get("sub"),
        "name": user.get("name"),
        "email": user.get("email"),
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
    user = request.session.get("user")

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return user