"""Keep project roles attached to the ZITADEL organization that granted them."""

from fastapi import HTTPException

APP_ROLES = frozenset({"viewer", "analyst", "admin"})


def organization_roles(role_data: object) -> dict[str, list[str]]:
    organizations: dict[str, list[str]] = {}
    if not isinstance(role_data, dict):
        return organizations
    for role, grants in role_data.items():
        if (
            not isinstance(role, str)
            or role not in APP_ROLES
            or not isinstance(grants, dict)
            or not grants
            or not all(
                isinstance(org, str)
                and 0 < len(org) <= 255
                and org.strip() == org
                and isinstance(domain, str)
                and bool(domain.strip())
                for org, domain in grants.items()
            )
        ):
            continue
        for org in grants:
            organizations.setdefault(org, []).append(role)
    return {org: sorted(roles) for org, roles in organizations.items()}


def scoped_identity(user: dict, organization_id: str | None = None) -> dict:
    """Select one verified grant; never combine different companies' roles.

    Old cookies containing only flattened roles deliberately have no access.
    Explicit company IDs are selectors, not authorization credentials.
    """
    grants = user.get("organization_roles", {})
    if not isinstance(grants, dict):
        grants = {}
    valid = {
        org: roles
        for org, roles in grants.items()
        if isinstance(org, str)
        and org
        and isinstance(roles, list)
        and roles
        and all(isinstance(role, str) and role in APP_ROLES for role in roles)
    }
    selected = organization_id or user.get("organization_id")
    if selected is not None and not isinstance(selected, str):
        raise HTTPException(403, "Invalid company selector")
    if selected is None and len(valid) == 1:
        selected = next(iter(valid))
    if selected is not None and selected not in valid:
        raise HTTPException(403, "No role in the selected company")
    subject = user.get("sub")
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise HTTPException(401, "Authentication required")
    return {
        **user,
        **(
            {"company_claims_missing": True}
            if "organization_roles" not in user
            and isinstance(user.get("roles"), list)
            and all(isinstance(role, str) for role in user["roles"])
            and APP_ROLES.intersection(user["roles"])
            else {}
        ),
        "organization_roles": valid,
        "organization_id": selected,
        "roles": list(valid.get(selected, [])),
    }
