# Threat model – Einsatzcockpit (ECP)

## What this project does and where untrusted input enters

Einsatzcockpit is a multi-tenant web application for fire brigades and other emergency
organisations (Austria/Germany). It is used for live incident command: alarms and dispatch,
incident boards, large-scale incidents ("GSL/Lage") with sites, units and sectors, radio log,
breathing-apparatus monitoring, road closures, objects/hydrants, contacts, vehicle logbook,
mailings, and more. Stack: FastAPI + SQLAlchemy (MariaDB in production, SQLite in tests),
Jinja2 + HTMX + Alpine.js, WebSockets, background loops in the app process.

**Tenancy**: every organisation (`fire_dept`, `org_id`) is a tenant. A user belongs to one
org; only `system_admin` is cross-org. Tenant filtering for SELECTs is done by a global
SQLAlchemy listener (`app/core/tenant.py`); **UPDATE/DELETE and anonymous/public routes are
not covered by that listener** and must scope themselves (see `CLAUDE.md`).

Untrusted input enters through:

- **Authenticated web UI and JSON/HTMX endpoints** (`app/routers/ui_*.py`). Session cookie
  plus CSRF token (`app/middleware/csrf.py`). Roles: `system_admin`, `admin` (org admin),
  `incident_leader`, `recorder`, `readonly`, plus feature roles (`fahrtenbuch_admin`,
  `mailing_admin`, `mailing_sender`, `objekt_verwalter`, `kontakt_verwalter`,
  `probenverwalter`, `breathing_supervisor`). Unit tablets log in as *device users*
  (`is_device`), and there is a read-only unit mode (`gsl_nur_lesen`).
- **Anonymous token/QR/PIN routes**. Each must only ever expose data of the org bound to
  the token:
  - `/alarm/{token}`
  - `/oeffentlich/strassensperre(n)/{token}`
  - `/infoscreen/strassensperren/{token}`
  - `/gk` (`/gk/einloesen`, `/gk/pin`, `/gk/pruefen`: personal and QR access for unit
    leaders)
  - `/pin-login` (SMS PIN)
  - public object maintenance (`objektpflege_public.py`)
  - public exercise planning (`ui_probenplanung_public.py`)
  - mailing tracking (`public_mailing_tracking.py`)
  - `/print/render/{job_id}`, `/print/artifacts/{job_id}`
  - Teams bot media/map tokens
- **Device and gateway APIs** with bearer/device tokens:
  - `device_api.py` (Android app: duty state, location, FCM token, push ack)
  - `gateway_api.py` (print/alarm gateway pairing and jobs)
  - `api_v1.py`
  - contact sync
- **Webhooks**:
  - Resend inbound mail (`/mail/webhook/resend-inbound` system-wide and
    `/mail/webhook/resend-inbound/{org_token}`, Svix signatures)
  - Resend mailing events (`/mailing/webhook/resend/{org_token}`)
  - Teams bot
  - LIS/DIBOS/BMA imports (`app/services/lis`, `dibos`, BMA import)
- **Remote MCP server** (`app/mcp/`, bearer token per user) exposing tools for objects,
  contacts, road closures, logbook, GSL resources, and document upload/download.
- **Inbound e-mail content** (`app/services/resend_inbound_service.py`,
  `mail_zuordnung_service.py`). Fully attacker-controlled sender, subject, HTML and
  attachments. It becomes incident messages and media, and can be auto-printed. A
  sysadmin-configured **mail forwarding relay** (`mail_weiterleitung_service.py`) re-sends
  inbound mail to configured targets.
- **File uploads** (images, PDF, video, generic `datei` attachments) via `media_service.py`,
  object documents, logbook photos, drone media. These are stored on disk under
  `MEDIA_STORAGE_DIR`, with thumbnails via Pillow/ffmpeg/poppler/tesseract.
- **Server-side rendering**:
  - PDF/print via WeasyPrint (`print_artifact_service.py`, `pdf_service.py`), where HTML
    templates can include URLs and images
  - map tiles and static maps
  - outbound HTTP to configured providers (Resend, SMS gateway, weather, routing, geocoding)

## Components that matter most / least

Most important:

1. **Tenant isolation**: any way for org A to read or modify org B's data, including via
   public tokens, MCP tools, WebSockets (`/ws/lage/{id}`), media/file downloads, print jobs,
   exports/backups (`org_export_service.py`, `org_import_service.py`), webhooks or background
   loops.
2. **Authentication and authorization**:
   - session handling, SSO, PIN login, password reset, invitations, account switching
   - device tokens, GK/QR tokens (HMAC-derived, `gk_zugang_service.py`)
   - role checks (`app/core/permissions.py`, `require_role`)
   - CSRF
3. **Alarm/dispatch integrity**: forging or suppressing alarms, SMS/push sending
   (`alarm_outbox`, SMS gateway, push), print-gateway jobs.
4. **Injection**: stored XSS in boards/journals/messages (content is shown live to many
   users), SQL injection, template injection, path traversal in media/storage paths, SSRF in
   rendering, attachment download or provider URLs, header injection in outgoing mail,
   open-relay abuse of mail forwarding.
5. **Secrets**: API keys and webhook secrets are stored encrypted (`app/core/crypto.py`).
   Leakage into logs, HTML, audit records or print payloads is a finding.

Less important or out of scope:

- `deploy/`, `docker-compose.yml`, `e2e/`, `scripts/` (operational tooling) and `docs/`.
- Third-party dependencies, unless a reachable path in ECP makes them exploitable.
- Test-only code under `tests/`.

## How to exercise it

- Python test suite: `pytest -q` in `/src`. It uses SQLite `./test.db`; set up in
  `tests/conftest.py`, which seeds roles and data.
- Good starting points for reproducers:
  - `tests/test_public_tenant_isolation.py` (pattern: token of org A must not reveal org B)
  - `tests/test_resend_posteingang*.py` (webhooks)
  - `tests/test_ressourcenkarte_*` and `tests/test_gk_*` (GK/QR access)
  - `tests/test_mail_weiterleitung.py` (mail relay)
  - `tests/test_mcp_*` (MCP)
- Tests use `fastapi.testclient.TestClient(app.main.app)`. Users are created directly in the
  DB, with password `Test1234!` and login via `POST /login`.
- JS tests: `npm run test:js` (offline outbox of the unit tablet mode).

## How you rate severity

- **Critical**:
  - unauthenticated access to another org's data or to personal data (phone numbers,
    patient/rescued-person data)
  - authentication bypass
  - remote code execution
  - unauthenticated triggering or suppression of alarms, SMS or pushes
  - takeover of a system_admin account
- **High**:
  - cross-org read/write with a valid login of any role
  - privilege escalation between roles, for example `readonly`/`recorder` to `admin`,
    device/GK/QR token to normal user, or org `admin` to `system_admin`
  - stored XSS visible to other users
  - SSRF reaching internal networks or cloud metadata
  - use of the mail forwarding as an open relay
  - path traversal reading or writing files outside the media directory
  - secret leakage (API keys, webhook secrets, tokens)
- **Medium**:
  - authorization flaws inside one org across roles without escalation to admin
  - CSRF with real effect
  - reflected XSS
  - token reuse after revocation
  - missing rate limits on authentication endpoints
- **Low**:
  - information leaks without personal data or secrets
  - missing security headers
  - verbose errors
- DoS only counts if it is cheap and unauthenticated. Then rate it Medium, otherwise Low.

## Reports and patches

- Please include a pytest reproducer in the style of the existing tests (TestClient, SQLite)
  and a minimal patch.
- UI texts and code comments are in German. Keep that in patches.
- Mutations on tenant tables must filter by `org_id` explicitly, and public routes must scope
  via the token's `org_id` (see `CLAUDE.md`).

## Anything to leave alone

- Do not contact real external services. Resend, SMS gateways, Microsoft Graph/Teams, LIS,
  DIBOS, weather and routing providers are all mocked in tests.
- Settings with `TEST_SYSTEM`/`DEBUG` relax cookie security for local testing only. Findings
  that only apply with `DEBUG=true` are out of scope.
