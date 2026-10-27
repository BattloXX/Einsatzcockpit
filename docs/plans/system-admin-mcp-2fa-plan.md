# System-Admin-MCP mit optionaler 2FA – Umsetzungsplan

Stand: 2026-10-27, geprüft gegen `main` (f80922b8, APP_VERSION 2026.10.26).
Quelle: Konzept "ECP System-Admin-MCP 2FA Umsetzungskonzept" (2026-10-27).

## Entscheidungen

1. Eigene OAuth-Tabellen (`admin_mcp_oauth_*`) und eigener `MCPServer` unter `/mcp/admin`, Scope `mcp-admin`.
2. Schreibende Admin-Tools werden nur registriert, wenn `admin_2fa_enabled` aktiv ist (einzige Step-up-Variante).
3. Verlust aller Faktoren: CLI-Reset am Server (`app/cli.py`), kein Mail-Bypass.
4. GitHub-Token für Phase 5 noch offen; Phase 5 startet notfalls nur mit SHA und Version.

## Prüfbefunde (Konzept gegen Code)

- Keine 2FA vorhanden (kein TOTP, keine Recovery-Codes). Systemeinstellungen: `/admin/system-einstellungen` (`ui_admin.py`, `SystemSettings` Key-Value).
- Session = zustandsloser signierter Cookie (`sign_session`/`unsign_session`); kein serverseitiger Widerruf. Lösung: `User.session_epoch` + MFA-Marker im Cookie, zentrales Gate in der Auth-Middleware (`main.py` ~578).
- Sessions entstehen an mehreren Stellen: `auth.py`, `sso.py`, `ui_account_switch.py`, `ui_pin_login.py`, MCP-OAuth-Login. Gate daher zentral, nicht je Route.
- Org-MCP ist org-gebunden (`load_live_context` und `/mcp/anmelden` verlangen `org_id`, `mcp_oauth_token.org_id` NOT NULL, `mcp_effective_enabled`). Admin-MCP braucht eigenen Kontext ohne Org-Zwang.
- `admin_mcp_access` existiert nicht (neu, Default false).
- `LeaderLock` ist ein flock pro Host; mehrere Leader über Hosts sind kein Fehler.
- Keine Deploy-SHA zur Laufzeit, nur `APP_VERSION`.
- Monitoring (`monitoring_api`, `dienst_monitor_service`) ist org-gebunden; globale Checks müssen org-los herausgezogen werden.
- Tenant-Listener filtert nur SELECTs; Reparaturen müssen explizit org-scopen, Diagnose nutzt `include_all_tenants=True`.
- Retries für SMS/Teams/Mail über die bestehende Alarm-Outbox (#446-#448).

## Phasen

- **Phase 0**: `GIT_SHA`/`BUILD_TIME` (Dockerfile, docker-build.yml, Settings), org-lose Diagnose-Services extrahieren.
- **Phase 1**: TOTP (Default AUS), Recovery-Codes, `session_epoch`, zentrales MFA-Gate, UI unter System-Einstellungen, CLI-Reset, Audit, Tests.
- **Phase 2**: Admin-MCP `/mcp/admin` read-only (eigener Provider, Login mit TOTP, 7 Diagnose-Tools).
- **Phase 3**: Diagnose-Engine (`diag_run`, `diag_finding`), Integritätschecks, 4 Tools.
- **Phase 4**: Repair-Registry (nur bei aktiver 2FA), Dry-Run, Idempotency, `admin_approval`.
- **Phase 5**: Codeanalyse, CI-Status, Codex-Auftrag (GitHub-Token offen).
- **Phase 6**: Systemdiagnose-UI, tägliche Diagnose.

Reihenfolge: 0 -> 1 -> 2 -> 3 -> 4 -> 6, Phase 5 parallel ab Phase 0.
Jede Phase ein PR; vor Merge ruff, mypy, pytest -n auto, MariaDB-Migrationsjob.
