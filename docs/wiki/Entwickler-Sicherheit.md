# Sicherheit

← [Zurück zur Startseite](Home)

Die MCP-spezifischen OAuth-, PKCE- und Live-Berechtigungsregeln beschreibt [MCP-Server erweitern](Entwickler-MCP-Erweiterung).

## Authentifizierung und Session

- Passwörter: **bcrypt** (12 Runden)
- SSO-ID-Token: Validierung mit **PyJWT[crypto]** in `app/services/sso_service.py`
  (RS256, Signatur über JWKS sowie Prüfung von Issuer, Audience und Ablaufzeit).
- Session-Token: signiert mit **itsdangerous** (HMAC-SHA1), Max-Age 24 h und Inaktivitäts-Timeout 8 h (Sliding Window)
- Brute-Force-Schutz: konfigurierbare Anzahl Fehlversuche sperrt das Konto für eine konfigurierbare Dauer (`LOGIN_MAX_FAILED`, `LOGIN_LOCKOUT_MINUTES`)
- `COOKIE_SECURE=true` wird in Produktion erzwungen (HTTPS)
- `FERNET_KEY` wird in Produktion als eigener, von `SECRET_KEY` unabhängig rotierbarer Datenschlüssel erzwungen
- `validate_startup_secrets()` in `app/config.py` prüft `SECRET_KEY`, `COOKIE_SECURE` und `FERNET_KEY` beim Start. In Produktion (`DEBUG=false`) bricht die App mit `RuntimeError` ab, wenn eine Angabe fehlt. Siehe [Installation-Troubleshooting](Installation-Troubleshooting).

## CSRF

- `CSRFMiddleware` verwendet das Double-Submit-Cookie-Pattern.
- Zustandsändernde POST-Requests werden geprüft; der reine CSP-Meldeendpunkt
  `POST /csp-report` ist CSRF-frei (siehe unten).
- `app/static/js/csrf.js` setzt den CSRF-Token automatisch im HTMX-Header.

## Content-Security-Policy

`app/middleware/security_headers.py` setzt die aktive `Content-Security-Policy`.
Die Basis ist `default-src 'self'`; Skripte und Styles kommen grundsätzlich von
der eigenen Instanz, mit gezielten Ausnahmen etwa für Karten und aktiviertes Turnstile.
`script-src 'self' 'unsafe-inline' 'unsafe-eval'` und
`style-src 'self' 'unsafe-inline'` erlauben die bestehenden Inline-Skripte,
Event-Handler und Styles. `'unsafe-eval'` wird für die Ausdrucksauswertung des
Alpine-Standard-Builds benötigt.

Infoscreens haben eigene Policies: Wetter/Statistik erlauben das Tailwind-CDN,
Alarm-Infoscreens für die URL-Rotation fremde HTTPS-Seiten in Frames.
`TRUSTED_FRAME_ANCESTORS` erlaubt konfigurierte Eltern-Origins für die Einbettung
der App; öffentliche Fahrtenbuch-Routen erlauben `frame-ancestors *`.

Mit `CSP_REPORT_ONLY=true` in `.env` wird zusätzlich
`Content-Security-Policy-Report-Only` gesendet (Standard: `false`). Die Mess-Policy
entspricht der jeweils aktiven CSP, entfernt aber nur `'unsafe-inline'` aus
`script-src` und ergänzt `report-uri /csp-report`. `'unsafe-eval'` bleibt wegen
Alpine erlaubt. Die aktive CSP wird dabei weiter durchgesetzt; die strengere
Mess-Policy meldet Inline-Skripte und Inline-Handler, ohne sie zusätzlich zu blockieren.

Browser senden Berichte an das anonyme, CSRF-freie `POST /csp-report`
(`app/routers/csp_report.py`, 60 Requests/Minute, maximal 16 KiB pro Bericht).
Der Logger `einsatzleiter.csp` aggregiert nach Direktive, blockierter Quelle und
Dokumentpfad; gleiche Kombinationen werden höchstens stündlich protokolliert.
Quellen werden auf ihre Origin gekürzt, numerische Pfadsegmente anonymisiert.
In der Sysadmin-Log-Ansicht nach diesem Logger auswerten und betroffene Templates
priorisieren. Der nächste Härtungsschritt sind Nonces für Skripte, der Abbau von
Inline-Handlern und die Umstellung auf den Alpine-CSP-Build.

## Multi-Tenant-Isolation

- Jeder Benutzer gehört einer Organisation (`org_id`) an.
- Abfragen mit `db.query()` werden durch das SQLAlchemy-`do_orm_execute`-Event automatisch nach `org_id` gefiltert.
- `db.get()` umgeht den Event-Handler; Router müssen geladene Objekte daher zusätzlich mit `same_org_or_system_admin()` prüfen.
- Die Einsatz-Kollaboration erfolgt über `IncidentOrg`; nur explizit eingeladene Organisationen sehen gemeinsame Einsätze (`visible_incidents_q()`).
- Systemadministratoren sehen alle Organisationen, Organisationsadministratoren nur ihre eigene.

Weitere technische Details: [Architektur](Entwickler-Architektur).

## Medien-Sicherheit

- Dateien liegen außerhalb von `app/static/` unter `app_storage/incident_media/` und sind nicht direkt per HTTP erreichbar.
- Die Auslieferung erfolgt ausschließlich über `/medien/datei/{id}` mit vollständiger Authentifizierungs- und Organisationsprüfung.
- `filetype` validiert den MIME-Typ anhand der tatsächlichen Datei-Bytes.
- UUID-basierte Dateinamen verhindern Path-Traversal über Originaldateinamen.

## Rate-Limiting

- Standard: 300 Requests pro Minute für alle Endpunkte
- `POST /login`: `LOGIN_RATELIMIT` (Standard `10/minute`), IP-basiert
- `POST /api/v1/einsatz`: `API_ALARM_RATELIMIT` (Standard `60/minute`), pro API-Key
- Medien-Upload: `UPLOAD_RATELIMIT` (Standard `20/minute`), IP-basiert
- `POST /kontakt`: `CONTACT_RATELIMIT` (Standard `5/hour`), IP-basiert

## Öffentliches Kontaktformular (`POST /kontakt`)

Mehrstufiger Spam-Schutz, angewendet in dieser Reihenfolge (`app/routers/public.py::contact_submit`):

1. **Honeypot**: Verstecktes Feld `website` — ist es befüllt, wird die Anfrage still verworfen (Redirect wie bei Erfolg, kein Mailversand).
2. **URL-Filter im Namensfeld**: Enthält `name` ein URL-Muster (`https?://` oder `www.`), wird die Anfrage ebenfalls still verworfen. Legitime Nutzer tragen nie eine URL im Namen ein — dieses Muster stammt von einem beobachteten Spam-Bot.
3. **Pflichtfeld-Check**: Fehlt `name`, `email` oder `message`, wird ein Fehler angezeigt (kein stiller Drop, da hier ein Mensch tippt).
4. **Rate-Limit**: `CONTACT_RATELIMIT`, IP-basiert (siehe oben).
5. **Cloudflare Turnstile** (optional): Nur aktiv, wenn `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` gesetzt sind. Serverseitige Prüfung via `_verify_turnstile()` gegen `https://challenges.cloudflare.com/turnstile/v0/siteverify`; technische Fehler (Timeout, Netzwerk) werden als fehlgeschlagen gewertet (fail closed). Ohne gesetzten `TURNSTILE_SECRET_KEY` läuft das Formular unverändert ohne CAPTCHA (kein Widget im Template, kein Server-Check).

Turnstile-Keys erzeugen: `dash.cloudflare.com` → Turnstile → Widget-Typ "Managed" oder unsichtbar, Domain der Instanz eintragen. Details in `.env.example`.

## API-Key-Sicherheit

- Keys werden ausschließlich als SHA-256-Hash gespeichert.
- Der Vergleich erfolgt timing-sicher mit `hmac.compare_digest`.
- Ablaufdatum und Widerruf werden unterstützt; jeder Key ist einer Organisation zugeordnet.

Weitere API-Details: [REST-API](Entwickler-REST-API).
