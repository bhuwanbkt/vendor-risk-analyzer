from collections.abc import Callable

import hmac

from fastapi import HTTPException, Request, status



def get_current_user(request: Request) -> dict:
    user = request.session.get("user")

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return user


def require_roles(
    *allowed_roles: str,
) -> Callable:

    def dependency(request: Request) -> dict:
        user = get_current_user(request)

        user_roles = set(
            user.get("roles", [])
        )

        if not user_roles.intersection(
            allowed_roles
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return user

    return dependency

def verify_csrf(request: Request) -> None:
    session_token = request.session.get("csrf_token")
    header_token = request.headers.get("X-CSRF-Token")

    if not session_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )

    if not hmac.compare_digest(
        session_token,
        header_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )