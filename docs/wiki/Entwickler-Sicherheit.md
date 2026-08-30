# Sicherheit

← [Zurück zur Startseite](Home)

## Authentifizierung und Session

- Passwörter: **bcrypt** (12 Runden)
- Session-Token: signiert mit **itsdangerous** (HMAC-SHA1), Max-Age 24 h und Inaktivitäts-Timeout 8 h (Sliding Window)
- Brute-Force-Schutz: konfigurierbare Anzahl Fehlversuche sperrt das Konto für eine konfigurierbare Dauer (`LOGIN_MAX_FAILED`, `LOGIN_LOCKOUT_MINUTES`)
- `COOKIE_SECURE=true` wird in Produktion erzwungen (HTTPS)
- `FERNET_KEY` wird in Produktion als eigener, von `SECRET_KEY` unabhängig rotierbarer Datenschlüssel erzwungen
- `validate_startup_secrets()` in `app/config.py` prüft `SECRET_KEY`, `COOKIE_SECURE` und `FERNET_KEY` beim Start. In Produktion (`DEBUG=false`) bricht die App mit `RuntimeError` ab, wenn eine Angabe fehlt. Siehe [Installation-Troubleshooting](Installation-Troubleshooting).

## CSRF

- `CSRFMiddleware` verwendet das Double-Submit-Cookie-Pattern.
- Alle zustandsändernden POST-Requests werden geprüft.
- `app/static/js/csrf.js` setzt den CSRF-Token automatisch im HTMX-Header.

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
