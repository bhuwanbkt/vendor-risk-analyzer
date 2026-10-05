from collections.abc import Callable

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