# MCP tools

The app can serve external AI clients over Streamable HTTP at `/mcp` using the
official MCP Python v2 SDK. It supports the modern 2026-07-28 protocol and legacy
initialize-based clients. Every protocol request needs a verified ZITADEL JWT
access token in its Authorization header. Browser session cookies grant no MCP
access.

## Enable the endpoint

Keep MCP disabled until you have configured [API authentication](api-authentication.md).
In your private `.env`, or your hosting service's environment variables, set:

```dotenv
API_BEARER_ENABLED=true
MCP_ENABLED=true
MCP_PUBLIC_URL=https://your-app.example/mcp
```

Use the exact URL clients will connect to, with no trailing slash, query, or
fragment. HTTPS is required outside development. HTTP loopback development can
use `http://localhost:8000/mcp`. Restart the application after editing settings.
MCP runs in the existing app process; it needs no extra container or public port.
When disabled, `/mcp` and its metadata route return 404.

Only the configured URL's Host and Origin are accepted. An Origin header can be
absent for native clients. Reverse proxies must preserve the public Host.
The request body limit is 64 KiB. Connections keep no MCP session identity;
each request is authenticated again. Tokens and caller-supplied URLs never become
downstream credentials or document locations.

## Tools and permissions

| Tool | Arguments | Roles |
| --- | --- | --- |
| `list_vendors` | Optional `limit`, `offset` | Viewer, Analyst, Admin |
| `get_vendor` | `vendor_id` | Viewer, Analyst, Admin |
| `list_vendor_documents` | `vendor_id`; optional pagination | Viewer, Analyst, Admin |
| `list_vendor_assessments` | `vendor_id`; optional pagination | Viewer, Analyst, Admin |
| `get_assessment` | `vendor_id`, `assessment_id` | Viewer, Analyst, Admin |
| `ask_vendor` | `vendor_id`, `question` | Analyst, Admin |

Tool role keys are the same lowercase `viewer`, `analyst` and `admin` used by the
app. No-role users receive 403 before any MCP messages are processed.
The tool schema includes UUID types, output schemas, and bounded arguments.
Lists return `items` and `next_offset`. The default limit is 50, the maximum is
100, and offsets range from 0 to 10,000.

Document tools return metadata without private object keys, signed URLs, or
file contents. Assessment reports use the same persisted summary, findings, and
risk fields as the HTTP API. A report must match both the supplied vendor ID and
assessment ID. Every vendor-specific query stays within that vendor.

`ask_vendor` calls the existing evidence-grounded chat service and Gemini; the
question is limited to 2,000 characters. It does not save a chat history or create
an assessment. Its tool annotation indicates an external service call and possible
different answers on repeated calls. The other five tools only read saved data.
MCP has no upload, creation, deletion, arbitrary SQL, or arbitrary URL tools.

Application roles currently grant access to all vendors. Vendor filtering keeps
an individual result in its selected vendor; it does not create per-user ownership
or tenant permissions.

## Connect a client

For a client supporting bearer headers, configure:

- Transport: Streamable HTTP
- URL: the exact `MCP_PUBLIC_URL`
- Header: `Authorization: Bearer <access_token>`

Use a ZITADEL JWT `access_token`, not an ID token, PAT, or opaque token. The token
must include the configured project audience, project-specific role grants, and
ZITADEL's `azp` OAuth client ID. Set JWT access tokens in the OAuth client's Token
Settings and obtain a token through its supported OAuth flow.

The server publishes public OAuth Protected Resource Metadata at
`/.well-known/oauth-protected-resource/mcp`. Its 401 challenge points to that URL.
The document identifies the configured ZITADEL issuer and these requested scopes:

```text
openid
urn:zitadel:iam:org:projects:roles
urn:zitadel:iam:org:project:id:<PROJECT_ID>:aud
```

Clients that manage browser OAuth need a client registration and callback URI
accepted by your ZITADEL instance. Pre-register an appropriate client when needed;
the app does not register clients or issue tokens. Do not assume a client's
automatic sign-in works until tested with your instance.

ZITADEL uses project identifiers for `aud` and currently ignores URL `resource`
indicators. The shared verifier checks the configured project audience explicitly;
the SDK's URL-audience check is disabled for this provider integration. Authorization
comes from the signed project role grants rather than invented OAuth role scopes.
See [ZITADEL scopes](https://zitadel.com/docs/apis/openidoauth/scopes),
[claims](https://zitadel.com/docs/apis/openidoauth/claims), and
[client registration limits](https://zitadel.com/docs/guides/integrate/dynamic-client-registration).

## Python client example with uv

Run `uv sync --python 3.12 --frozen --no-dev` first. Make `MCP_URL` and a freshly
issued `ACCESS_TOKEN` available in your private shell environment. The installed
SDK can list tools and read the first vendor page:

```python
import asyncio
import os

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]},
        follow_redirects=False,
    ) as http:
        transport = streamable_http_client(os.environ["MCP_URL"], http_client=http)
        async with Client(transport) as client:
            tools = await client.list_tools()
            print([tool.name for tool in tools.tools])
            result = await client.call_tool("list_vendors", {"limit": 10})
            print(result.structured_content)


asyncio.run(main())
```

Save the example locally and run it with `uv run --python 3.12 python <script.py>`.
Keep real tokens out of committed files. This example supplies a token; it does not
refresh it or run an interactive OAuth login.

## Verification

```sh
uv run --python 3.12 --with pytest pytest -q tests/security/test_mcp.py
```

The tests use real RSA signatures and trusted-key HTTP mocks, seeded vendor data,
the app lifespan, and the real MCP HTTP transport. They check discovery, both
protocol generations, roles, cookie rejection, headers, Host/Origin, provider
outages, input/body limits, vendor/report separation, and grounded-question cleanup.
The full suite also covers the shared JWT verifier's claims, key caching and rotation.

Actual external-client OAuth login, ZITADEL-issued tokens, and Gemini responses
still need a live integration check after deployment.
