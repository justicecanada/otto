# Otto API foundation notes

This directory currently contains the first cut of Otto's generalized API foundation.

## Browsing the API

When signed in as an Otto admin, you can browse the API from:

- `/api/v1/` — Swagger UI
- `/api/v1/schema/` — OpenAPI schema
- `/api/v1/redoc/` — ReDoc

## What was added

The branch now supports two API caller types:

1. **Human callers** using the existing Django session
2. **Machine callers** using managed bearer tokens

The intent is to keep browser/admin workflows simple while making room for machine-to-machine integrations without hard-coding a single sync-specific auth path.

## Current auth model

For `/api/v1/...` requests:

- If an `Authorization: Bearer ...` header is present, Otto treats the request as a **machine client** request.
- Otherwise, Otto falls back to the normal Django authenticated user session.
- Browser-style login redirects and terms-of-use redirects are bypassed for API paths so API endpoints return JSON errors instead of HTML redirects.

### Important behavior

- **Bearer token auth takes precedence** over session auth when both are present.
- Machine clients are checked for:
  - active status
  - valid secret
  - required endpoint scopes
  - optional source IP allowlist
- Human callers are checked using normal Django permissions.

## Machine client control plane

Machine clients are managed through:

- `/user_management/api_clients/`

The management UI currently supports:

- registering a machine client
- assigning endpoint scopes
- configuring allowed IP ranges / CIDRs
- enabling or disabling a client
- rotating the bearer token
- reviewing an append-only audit trail of configuration changes

## Models involved

These models live in `django/otto/models.py`:

- `ApiClient`
- `ApiClientScope`
- `ApiClientAllowedIP`
- `ApiClientAuditEvent`

## Scope registry

API scopes are defined in:

- `django/otto/utils/api_permissions.py`

Current scope:

- `reporting.user_activity.read`

This scope currently unlocks:

- `GET /api/v1/reporting/user-activity/summary/`
- `GET /api/v1/reporting/user-activity/users/`

## Reporting endpoint behavior

The reporting endpoints in `django/otto/api/views.py` now allow either:

- a human caller with `otto.manage_users`
- or a machine client with `reporting.user_activity.read`

## Audit trail behavior

Every machine-client change should produce an audit event:

- client created
- client updated
- secret rotated

The audit trail is designed to capture configuration changes such as:

- scope additions/removals
- allowed IP additions/removals
- owner changes
- status changes
- other edited fields

## Manual verification checklist

These checks assume the Django dev server is running on `http://127.0.0.1:8000`.

### 1. Create a machine client in the UI

1. Sign in as an Otto admin.
2. Open `Manage users`.
3. Open `API Clients`.
4. Create a client with:
   - a recognizable name
   - scope `Read user activity reporting API`
   - optionally `127.0.0.1/32` as an allowed IP when testing locally
5. Copy the one-time bearer token shown after creation.

Expected result:

- the client appears in the table
- the scope is shown
- the allowed IP appears if configured
- an audit event appears for creation

### 2. Verify session-auth access still works

While signed in as an admin in the browser, open:

- `/api/v1/reporting/user-activity/summary/?activity_type=any_usage&interval=month`
- `/api/v1/reporting/user-activity/users/?format=json`

Expected result:

- both endpoints return JSON
- no redirect to the welcome/login page occurs

### 3. Verify unauthenticated requests return JSON 401

From a terminal with no bearer token:

```bash
curl -i "http://127.0.0.1:8000/api/v1/reporting/user-activity/users/"

```

Expected result:

- status `401`
- body similar to:

```json
{"detail":"Authentication required."}
```

### 4. Verify bearer-token access works

Replace `<TOKEN>` with the one-time bearer token from the UI:

```bash
curl -i \
  -H "Authorization: Bearer <TOKEN>" \
  "http://127.0.0.1:8000/api/v1/reporting/user-activity/summary/?activity_type=any_usage&interval=month"
```

Expected result:

- status `200`
- JSON response body
- the machine client's `last_used_at` and `last_used_ip` update in the UI

### 5. Verify missing scope is rejected

Create a second machine client **without** assigning the reporting scope, then call:

```bash
curl -i \
  -H "Authorization: Bearer <TOKEN_WITHOUT_SCOPE>" \
  "http://127.0.0.1:8000/api/v1/reporting/user-activity/users/"
```

Expected result:

- status `403`
- body:

```json
{"detail":"Forbidden."}
```

### 6. Verify allowed-IP enforcement

Set a machine client's allowed IPs to something that does **not** include localhost, for example:

- `10.0.0.0/24`

Then call the same reporting endpoint from the local machine:

```bash
curl -i \
  -H "Authorization: Bearer <TOKEN>" \
  "http://127.0.0.1:8000/api/v1/reporting/user-activity/users/"
```

Expected result:

- status `403`
- body:

```json
{"detail":"Bearer token not allowed from this IP address."}
```

### 7. Verify rotation invalidates the old token

1. Use the UI to rotate the client secret.
2. Retry the API call with the old token.
3. Retry again with the newly displayed token.

Expected result:

- old token returns `401`
- new token returns `200`
- an audit event for secret rotation is shown

### 8. Verify audit trail details

After creating, editing, and rotating a client, review the audit table.

Expected result:

- create event shown
- update event shows field changes
- scope and allowed-IP changes are listed explicitly
- rotate event shown

## How to extend this foundation

When adding a new API endpoint family:

1. Add a new scope to `django/otto/utils/api_permissions.py`
2. Gate the endpoint with `api_permission_error(...)`
3. Decide whether the endpoint allows:
   - humans only
   - machines only
   - or both
4. Log principal information using `request.api_principal` when helpful
5. Add tests for:
   - session auth success/failure
   - bearer auth success/failure
   - scope enforcement
   - IP allowlist enforcement if applicable

## Current limitations

This is intentionally a baseline, not the final API platform.

Examples of future work that may still be useful:

- richer scope catalog
- machine client ownership workflows
- secret expiration policies
- usage dashboards for machine clients
- IP allowlist UX refinements
- endpoint-family-specific audit summaries
