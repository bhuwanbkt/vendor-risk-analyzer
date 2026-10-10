from __future__ import annotations

from vendor_risk_analyzer.auth.dependencies import get_user_roles


def build_navigation(
    user: dict,
) -> list[dict]:
    """
    Build navigation dynamically.

    Features the user cannot access are
    not included in navigation at all.
    """

    roles = get_user_roles(user)
    if not roles.intersection({"viewer", "analyst", "admin"}):
        return []

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
