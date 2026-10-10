# API access tokens

Browser pages use signed session cookies. External API clients can use ZITADEL JWT
access tokens when `API_BEARER_ENABLED=true`. The default is `false`, so existing
deployments do not enable a new authentication method automatically.

## Provider configuration

Use the same trusted HTTPS `ZITADEL_ISSUER` and `ZITADEL_PROJECT_ID` configured for
the app. Configure the client issuing access tokens to use **JWT** access tokens
in ZITADEL's Token Settings. Obtain an `access_token` through the client's
supported OAuth flow; an ID token is not an API credential.

Request these scopes, substituting the app's actual project ID:

```text
openid
urn:zitadel:iam:org:projects:roles
urn:zitadel:iam:org:project:id:<PROJECT_ID>:aud
```

The audience scope adds the project ID to `aud`. The plural projects-role scope
requests `urn:zitadel:iam:org:project:<PROJECT_ID>:roles`. Assign this project's
lowercase `viewer`, `analyst` or `admin` role to the authenticated user or service
account. Roles from another project or generic/flat role claims grant no access.
See ZITADEL's [scopes](https://zitadel.com/docs/apis/openidoauth/scopes),
[claims](https://zitadel.com/docs/apis/openidoauth/claims) and
[token settings](https://zitadel.com/docs/guides/manage/console/applications-overview#token-settings).

## Make a request

Set `ACCESS_TOKEN` in your local shell to an access token from your OAuth client,
then send it only in the Authorization header:

```sh
curl --fail-with-body \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/auth/me

curl --fail-with-body \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://localhost:8000/api/vendors
```

Use your HTTPS app URL outside localhost development. Do not put tokens in URLs,
source files or committed configuration. The app does not expose a token-minting
endpoint or return the browser's provider access token.

The same role matrix as browser sessions applies. A validated bearer request can
write without a CSRF token, because authentication comes from its explicit header.
Cookie-authenticated writes still require their session CSRF token. Bearer
credentials take precedence when both a header and a cookie are present; an
invalid token never falls back to the cookie's identity.

| Result | Meaning |
| --- | --- |
| 401 | Missing authentication, invalid token, or bearer mode disabled |
| 403 | Authenticated identity lacks a role required by the endpoint |
| 503 | Trusted signing keys are unavailable or expired and cannot be refreshed |

Bearer authentication applies to `/api/*` and `/auth/me`. It does not create a
browser session or authenticate HTML pages. User and tenant vendor ownership is
not modeled; authorized roles currently have access to all vendors.

## Verification and limits

The verifier checks the RS256 signature with public RSA keys from the fixed
`<ZITADEL_ISSUER>/oauth/v2/keys` endpoint. Token-provided key URLs are ignored.
It validates issuer, project audience, subject, access-token ID, expiry and issue
time, plus `nbf` when present, with 30 seconds of clock tolerance. ZITADEL ID/logout
tokens and opaque access tokens are rejected.

Signing keys are cached for five minutes; an unknown key ID can trigger an earlier
refresh, limited to one refresh per ten seconds. Expired cached keys are not used
when the provider is unavailable. JWT validation is local: revocation and role
changes are not checked on every request, so use short-lived provider access
tokens and refresh them after changing roles. Browser logout does not revoke a
separate API token.

Automated tests use real RSA signatures and mocked trusted-key HTTP responses.
Before enabling bearer mode on a hosted deployment, check actual ZITADEL-issued
JWTs for Viewer/Analyst/Admin and no-role identities. The optional
[MCP transport](mcp.md) reuses this verifier, requires bearer headers on every
protocol request, and adds fixed tools with per-call role checks.
