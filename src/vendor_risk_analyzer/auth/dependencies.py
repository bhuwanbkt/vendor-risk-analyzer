import hmac
from collections.abc import Callable

from fastapi import HTTPException, Request, status
from vendor_risk_analyzer.auth.organizations import scoped_identity


def get_current_user(request: Request) -> dict:
    if request.scope.get("api_bearer_authenticated") is True:
        values = request.headers.getlist("X-Organization-ID")
        if len(values) > 1 or (values and not values[0].strip()):
            raise HTTPException(400, "Invalid company selector")
        return scoped_identity(request.scope["api_bearer_user"], values[0] if values else None)
    user = request.session.get("user")

    if not isinstance(user, dict) or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return scoped_identity(user)


def get_user_roles(user: dict) -> set[str]:
    """Read the role list produced by the OIDC callback; reject malformed claims."""
    roles = user.get("roles", [])
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        return set()
    return set(roles)


def require_roles(
    *allowed_roles: str,
) -> Callable:

    def dependency(request: Request) -> dict:
        user = get_current_user(request)

        user_roles = get_user_roles(user)

        if not user_roles.intersection(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return user

    return dependency


def verify_csrf(request: Request) -> None:
    # Header tokens are verified before route dependencies; cookies still need CSRF.
    if request.scope.get("api_bearer_authenticated") is True:
        return
    session_token = request.session.get("csrf_token")
    header_token = request.headers.get("X-CSRF-Token")

    if not (
        isinstance(session_token, str)
        and session_token
        and session_token.isascii()
        and isinstance(header_token, str)
        and header_token
        and header_token.isascii()
        and hmac.compare_digest(session_token, header_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
