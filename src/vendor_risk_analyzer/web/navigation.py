from __future__ import annotations


def normalize_roles(
    user: dict,
) -> set[str]:
    raw_roles = user.get(
        "roles",
        [],
    )

    if isinstance(
        raw_roles,
        str,
    ):
        return {
            raw_roles.lower()
        }

    if isinstance(
        raw_roles,
        list,
    ):
        return {
            str(role).lower()
            for role in raw_roles
        }

    return set()


def build_navigation(
    user: dict,
) -> list[dict]:
    """
    Build navigation dynamically.

    Features the user cannot access are
    not included in navigation at all.
    """

    roles = normalize_roles(
        user
    )

    navigation = [
        {
            "label": "Dashboard",
            "href": "/dashboard",
            "key": "dashboard",
            "icon": "⌂",
        },
        {
            "label": "Vendors",
            "href": "/vendors",
            "key": "vendors",
            "icon": "◇",
        },
        {
            "label": "Documents",
            "href": "/documents",
            "key": "documents",
            "icon": "▣",
        },
        {
            "label": "Assessments",
            "href": "/assessments",
            "key": "assessments",
            "icon": "◈",
        },
    ]


    # Chat is available only to users who
    # can actively analyze vendor material.
    if (
        "analyst" in roles
        or "admin" in roles
    ):
        navigation.append(
            {
                "label": "AI Chat",
                "href": "/chat",
                "key": "chat",
                "icon": "✦",
            }
        )


    # System configuration is admin-only.
    if (
        "admin" in roles
    ):
        navigation.append(
            {
                "label": "System",
                "href": "/admin/system",
                "key": "system",
                "icon": "⚙",
            }
        )


    # Do NOT add MCP yet.
    #
    # Until the MCP endpoint and JWT/JWKS
    # authorization layer are complete,
    # users should not see an MCP menu item.


    return navigation