# Einsatzcockpit – Entwicklungsregeln

## Stack

- **Backend**: FastAPI, SQLAlchemy ORM, Jinja2
- **Frontend**: HTMX + Alpine.js, Tailwind (Utility-Klassen), Leaflet.js
- **Real-time**: WebSockets `/ws/lage/{lage_id}`, Broadcast via `broadcast_lage()`
- **Sprache**: Deutsch (Österreich) – UI-Texte, Kommentare, Variablennamen
- **Single-Tenant pro Org**: alle Lagen gehören zu einer `org_id`, keine Cross-Org-Queries

---

## Pflicht: Nur gerade ASCII-Anführungszeichen in Code

In Templates, JavaScript und allen Attributen ausschließlich `"` und `'` – niemals
Smart Quotes (`“ ” „ ‘ ’`). In Attributen (`hx-post`, `x-data`, `onclick` …) machen sie
das Markup ungültig, Formulare brechen stillschweigend (Vorfall 2026-06-19,
`incident_major/_site_detail.html`).

- Typografische Quotes nur im **sichtbaren Anzeigetext**.
- Editor-Autokorrektur beim Umordnen von Blöcken deaktiviert lassen.
- Vor Commit von Templates: `rg '[“”„‘’]' app/templates`

## Pflicht: Tenant-Scoping bei Mutationen und Public-Routen

Der Tenant-Listener (`app/core/tenant.py`) filtert **nur SELECTs**.

- **Kein Bulk `db.query(...).update()` / `.delete()`** auf Tenant-Tabellen – läuft
  ungefiltert. Stattdessen gefiltert laden, dann mutieren; falls Bulk unvermeidbar,
  explizit `.filter(Model.org_id == org_id)`.
- **Anonyme/öffentliche Endpunkte** (Token/QR/PIN/Signatur, SEC-11) laufen ohne
  Tenant-Filter und müssen selbst über ihre Beweiskette scopen
  (z. B. `.filter(... == token.org_id)`).
- **Jede neue Public-Route braucht einen Cross-Org-Test** in
  `tests/test_public_tenant_isolation.py` (Muster: Token Org A darf keine Daten Org B zeigen).

## Pflicht: Sofortige Darstellung nach Eingabe (kein F5)

Jede Formular-Aktion aktualisiert das UI ohne manuelles Neuladen.

- **Kein `location.reload()` nach HTMX-Requests** – gezielten Swap nutzen.
  (Legitim bleibt es nur für periodische Vollreloads: Infoscreen, Screensaver.)
- **Board-Karte** nach Änderung (Ressource, Prio, Foto) neu holen:
  ```javascript
  htmx.ajax('GET', '/lage/{lage_id}/stellen/{site_id}/card',
            { target: '[data-site-id="{site_id}"]', swap: 'outerHTML' })
  ```
- **Detail-Panel** nach Aktionen im Site-Detail-Modal:
  ```javascript
  htmx.ajax('GET', '/lage/{lage_id}/stellen/{site_id}',
            { target: '#siteDetailContent', swap: 'innerHTML' })
  ```
- **Listen-Partials** (Journal, Funkjournal): nur den Listen-Container reloaden.
- **WebSocket-Broadcast** nach jeder Mutation, die andere Nutzer betrifft:
  `site:card_changed` (mit `site_id`), `cross_marker:changed`, `staff:changed`.

## Pflicht: Zeitzonen (DB = UTC, Anzeige = Org-Zeitzone)

Die DB speichert **immer naive UTC**. Anzeige-Zeitzone pro Org (`FireDept.timezone`,
IANA-Name), Fallback `settings.DEFAULT_TIMEZONE = "Europe/Vienna"`.
Helper: `app/core/timezones.py`; Jinja-Filter `local`, `local_time`, `local_datetime`,
`local_datetime_sec`, `local_iso` (registriert in `app/core/templating.py`, lesen `user.org`).

| Situation | Richtig | Falsch |
|---|---|---|
| Datetime im Template | `{{ x\|local_datetime }}` | `{{ x.strftime(...) }}` |
| `datetime-local`-Input | `value="{{ (x\|local).strftime('%Y-%m-%dT%H:%M') }}"` | `value="{{ x.strftime(...) }}"` |
| Datetime in Python (PDF/CSV/XLSX) | `format_local_datetime(x, org)` | `str(x)` / `x.strftime(...)` |
| Form-Input speichern | `local_input_to_utc(wert, org)` | direkt speichern |
| Datumsfilter (Query-Range) | `local_date_to_utc(von, org=org)` | `local_date_to_utc(von)` |

- Reine `date`-Felder (`flug.datum`, `wartung.faellig_am`) → keine Konvertierung.
- JS-Konsum mit Z-Suffix (`strftime('%Y-%m-%dT%H:%M:%SZ')`) → korrekt, JS rechnet um.
- Service-Funktionen mit Datetime-Ausgabe bekommen `org` als Parameter.
- Vor Commit: `rg "\.strftime\(" app/templates` (nur date-only/Z-Suffix erlaubt) und
  `rg "str\(.*_at" app/services/uas_pdf.py`.
- Referenz-Implementierungen: `app/routers/ui_termin.py` + `app/templates/termin/`,
  `app/services/pdf_service.py`.

---

## Vor jedem Commit / Merge: alle drei CI-Checks

`.github/workflows/ci.yml` hat drei Jobs – pytest allein deckt nur einen ab:

```
.venv/bin/python -m ruff check app/
.venv/bin/python -m mypy app/ --ignore-missing-imports
.venv/bin/python -m pytest -q
```

- Immer `.venv/bin/python`; es gibt kein `python` im PATH.
- Nie zwei Testläufe parallel (gemeinsame SQLite-Datei → hunderte Scheinfehler).
  Laufenden Test prüfen mit `ps -eo args | grep "[p]ytest"`, nicht `pgrep pytest`.
- Voller Lauf ≈ 4 min. CI testet gegen **MariaDB inkl. `alembic upgrade head`**,
  lokal SQLite – Migrationen können lokal grün und in CI rot sein.

## Orientierung (Einsatzboard)

- Board-Karten: `app/templates/incident_major/_site_card.html`, Container mit
  `data-site-id`; zeigen aktive Ressourcen, Foto-Zähler, Priorität, Sektor.
- Übergreifende Meldungen (Cross-Marker): eigene Board-Spalte mit Mini-OSM-Karte,
  wenn `marker.lat`/`marker.lng` gesetzt; mobil über das Phasen-Dropdown, Wert `uebergreifend`.
- Suche ist client-seitig: Funkjournal über `data-fj-search`, Stab-Journal über
  `.journal-row`, Board über `applyBoardFilters()` in `incident_major/board.html`.

## Neue Features – Checkliste

- [ ] Mobile Ansicht (≤760px) berücksichtigt
- [ ] CSRF-Token (`_csrf`) in allen POST-Formularen
- [ ] HTMX-Swap statt Reload, WebSocket-Broadcast für Multi-User-Sync
- [ ] Alle Datetime-Ausgaben über `|local*` / `format_local_datetime(.., org)`
