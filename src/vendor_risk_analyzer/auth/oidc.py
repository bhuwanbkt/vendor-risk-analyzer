from authlib.integrations.starlette_client import OAuth

from vendor_risk_analyzer.config import get_settings


settings = get_settings()

oauth = OAuth()

oauth.register(
    name="zitadel",
    client_id=settings.zitadel_client_id,
    server_metadata_url=(
        f"{settings.zitadel_issuer.rstrip('/')}/.well-known/openid-configuration"
    ),
    client_kwargs={
        "scope": (
            "openid profile email urn:zitadel:iam:org:projects:roles "
            f"urn:zitadel:iam:org:project:id:{settings.zitadel_project_id}:aud"
        ),
        "code_challenge_method": "S256",
    },
)
