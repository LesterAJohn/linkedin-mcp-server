# FortisAI OpenAPI Facade

FortisAI can run this LinkedIn MCP server as a managed streamable HTTP upstream
and expose a local OpenAPI facade for OpenWebUI, curl, and other OpenAPI-native
clients. The facade is provided by the FortisAI deployment; this repository
provides the upstream MCP server image/source that the facade calls.

The upstream MCP server still listens on its MCP path, usually `/mcp`. The
FortisAI facade translates the OpenAPI routes below into MCP `tools/call`
requests.

## Base URLs

From the Linux host:

```text
http://127.0.0.1:8102
```

From containers on the FortisAI network:

```text
http://fortisai-mcp-openapi-linkedin.fortisai.local:8102
```

Useful discovery routes:

```text
GET /openapi.json
GET /docs
GET /healthz
```

## User Routing

FortisAI supports one default upstream container and optional per-user upstream
containers. To route a request to a user-specific LinkedIn browser profile, pass
the OpenWebUI user identity in a header or body field.

Recommended header:

```text
x-openwebui-user-email: {useremail}
```

Equivalent body field:

```json
{
  "openwebui_user_id": "{useremail}"
}
```

Accepted body routing fields:

```text
openwebui_user_id
openwebui_username
user_id
username
user
openwebui_user.id
openwebui_user.email
openwebui_user.username
openwebui_user.name
```

Accepted routing headers:

```text
x-openwebui-user-id
x-openwebui-user-email
x-openwebui-user-name
x-fortisai-openwebui-user
x-fortisai-user
x-user-id
```

If no user identity is supplied, the facade uses the default upstream container.
With `{useremail}`, FortisAI resolves the user segment to:

```text
lesterajohn-gmail-com-cd1d71bcd4
```

and routes to:

```text
fortisai-mcp-openapi-linkedin-upstream-lesterajohn-gmail-com-cd1d71bcd4
```

## Endpoints

| Method | Path | Body | MCP tool in streamable HTTP mode |
| --- | --- | --- | --- |
| `GET` | `/linkedin_connection_info` | none | diagnostic facade response |
| `POST` | `/linkedin_search_jobs` | `query`, optional `limit` | `search_jobs` |
| `POST` | `/linkedin_get_job` | `job_id` | `get_job_details` |
| `POST` | `/linkedin_search_people` | `query`, optional `limit` | `search_people` |
| `POST` | `/linkedin_get_profile` | `profile_id` | `get_person_profile` |
| `POST` | `/linkedin_get_company` | `company_id` | `get_company_profile` |
| `POST` | `/linkedin_get_feed` | optional `limit` | `get_feed` |
| `POST` | `/linkedin_get_connections` | optional `limit` | not mapped for `/mcp` upstreams |
| `POST` | `/linkedin_send_message` | `recipient_id`, `message` | `send_message` |
| `POST` | `/linkedin_close_session` | optional `reason` | `close_session` |

`/linkedin_get_connections` is exposed by the facade for compatibility, but the
current MCP streamable HTTP mapping does not map it to a downstream MCP tool. It
can return `501 unsupported_for_mcp_upstream` when the configured upstream URL is
`/mcp`.

Mutation routes such as `/linkedin_send_message` require the FortisAI facade to
start with `LINKEDIN_ALLOW_MUTATIONS=true`.

## Curl Examples

Set a base URL and user once:

```bash
BASE_URL="http://127.0.0.1:8102"
USER_EMAIL="{useremail}"
```

Connection info:

```bash
curl -s "$BASE_URL/linkedin_connection_info" \
  -H "x-openwebui-user-email: $USER_EMAIL"
```

Search jobs:

```bash
curl -s "$BASE_URL/linkedin_search_jobs" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"query":"Oracle AI engineer Dallas","limit":10}'
```

Get job details:

```bash
curl -s "$BASE_URL/linkedin_get_job" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"job_id":"1234567890"}'
```

Search people:

```bash
curl -s "$BASE_URL/linkedin_search_people" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"query":"AI platform engineer Dallas","limit":10}'
```

Get a profile:

```bash
curl -s "$BASE_URL/linkedin_get_profile" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"profile_id":"some-linkedin-username"}'
```

Get a company:

```bash
curl -s "$BASE_URL/linkedin_get_company" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"company_id":"Oracle"}'
```

Get the authenticated user's feed:

```bash
curl -s "$BASE_URL/linkedin_get_feed" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"limit":10}'
```

Send a message:

```bash
curl -s "$BASE_URL/linkedin_send_message" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"recipient_id":"some-linkedin-username","message":"Hello from FortisAI."}'
```

Close the browser session:

```bash
curl -s "$BASE_URL/linkedin_close_session" \
  -H "Content-Type: application/json" \
  -H "x-openwebui-user-email: $USER_EMAIL" \
  -d '{"reason":"session_cleanup"}'
```

## Response Shape

Successful proxied responses use this general shape:

```json
{
  "ok": true,
  "status": 200,
  "upstream_url": "http://fortisai-mcp-openapi-linkedin-upstream-...:8000/mcp",
  "user_id": "{useremail}",
  "path": "/linkedin_search_jobs",
  "mode": "mcp-streamable-http",
  "tool": "search_jobs",
  "data": {}
}
```

Errors are returned as normal HTTP error responses with a JSON `detail` object.
Common setup errors include `linkedin_upstream_not_configured`,
`linkedin_upstream_request_failed`, and `unsupported_for_mcp_upstream`.
