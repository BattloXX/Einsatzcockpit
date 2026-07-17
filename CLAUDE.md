# Einsatzcockpit – Entwicklungsregeln

## Stack

- **Backend**: FastAPI (Python), SQLAlchemy ORM, Jinja2 templates
- **Frontend**: HTMX + Alpine.js, Tailwind CSS (utility classes), Leaflet.js (maps)
- **Real-time**: WebSockets via `/ws/lage/{lage_id}` – broadcast mit `broadcast_lage()`
- **Sprache**: Deutsch (Österreich) – alle UI-Texte, Kommentare und Variablennamen

## Pflicht: Nur gerade ASCII-Anführungszeichen in Code

**In HTML/Jinja2-Templates, JavaScript und allen Code-Attributen ausschließlich gerade Anführungszeichen `"` und `'` verwenden – niemals typografische „Smart Quotes" (`“` `”` `„` `‘` `’`).**

- Smart Quotes in Attributen (`hx-post`, `name`, `id`, `style`, `onclick`, `x-data` …) machen das Markup ungültig → Formulare und Skripte funktionieren stillschweigend nicht mehr (z. B. Lagemeldung/Foto-Upload in `_site_detail.html`, Vorfall 2026-06-19).
- Typografische Anführungszeichen sind **nur im sichtbaren Anzeigetext** erlaubt (z. B. `Nur „Lagemeldung" …`), nie in Code/Attributen.
- Beim Umstellen/Umordnen von Blöcken: keine Autokorrektur/Editor-„Smart Quotes" aktiv lassen.
- Schnellcheck vor Commit von Templates: nach `“ ” „ ‘ ’` in Attributen suchen und durch `"` / `'` ersetzen.

## Architektur

- Single-Tenant pro Org: Alle Lagen gehören zu einer `org_id` – keine Cross-Org-Queries
- Templates verwenden HTMX für Teilupdates und Formulare
- Board-Cards (`.site-card`) haben `data-site-id` Attribute für gezieltes HTMX-Swap
- WebSocket-Events steuern Live-Updates ohne Page-Reload

## Pflicht: Tenant-Scoping bei Mutationen und Public-Routen

Der Tenant-Listener (`app/core/tenant.py`) filtert **nur SELECTs** automatisch.

- **Niemals `db.query(...).update()` / `.delete()` (Bulk) auf Tenant-Tabellen** –
  diese Statements laufen UNGEFILTERT. Stattdessen: Objekte erst (gefiltert)
  laden, dann mutieren. Falls Bulk unvermeidbar: expliziten
  `.filter(Model.org_id == org_id)` setzen.
- **Anonyme/öffentliche Endpunkte** (Token/QR/PIN/Signatur, SEC-11) laufen ganz
  ohne Tenant-Filter und müssen selbst über ihre Beweiskette scopen (z. B.
  `.filter(... == token.org_id)`).
- **Jede neue Public-Route braucht einen Cross-Org-Test** in
  `tests/test_public_tenant_isolation.py` (Muster dort: Token der Org A darf
  keine Daten der Org B preisgeben).

## Pflicht: Sofortige Darstellung nach Eingabe (kein F5)

**Jede Formular-Aktion muss das UI sofort aktualisieren – ohne manuelle Seitenaktualisierung.**

### Regeln für Formulare:

1. **Niemals `location.reload()` verwenden** nach HTMX-Requests. Stattdessen gezieltes HTMX-Swap nutzen.

2. **Board-Karten aktualisieren**: Wenn eine Aktion (Ressource zuweisen, Prio ändern, Foto hochladen) den Inhalt einer Board-Karte ändert, muss die Karte per HTMX-Swap aktualisiert werden:
   ```javascript
   htmx.ajax('GET', '/lage/{lage_id}/stellen/{site_id}/card', {
     target: '[data-site-id="{site_id}"]',
     swap: 'outerHTML'
   })
   ```

3. **Detail-Panel aktualisieren**: Aktionen im Site-Detail-Modal müssen das Panel neu laden:
   ```javascript
   htmx.ajax('GET', '/lage/{lage_id}/stellen/{site_id}', {
     target: '#siteDetailContent',
     swap: 'innerHTML'
   })
   ```

4. **Listen-Partials**: Für Journal/Funkjournal-Listen nach Eintrag → HTMX-Reload des Listen-Containers (nicht der ganzen Seite).

5. **WebSocket-Broadcasts**: Nach jeder Datenmutation, die andere Nutzer interessiert:
   - Board-Karten-Änderungen: `broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": site_id})`
   - Cross-Marker-Änderungen: `broadcast_lage(lage_id, {"type": "cross_marker:changed", ...})`
   - Stab-Änderungen: `broadcast_lage(lage_id, {"type": "staff:changed"})`

6. **Fotos/Medien**: Nach Upload sofort im Detail-Panel und in der Board-Karte (Foto-Zähler) aktualisieren.

## Board-Karten (\_site_card.html)

- Zeigen aktive Ressourcen (🚒 N), Foto-Zähler (📷 N), Priorität, Sektor
- Karten-Endpoint: `GET /lage/{lage_id}/stellen/{site_id}/card` liefert das Partial
- Prio-Schnellbuttons: Nach Klick Karte per HTMX-Swap aktualisieren (kein `location.reload()`)

## Übergreifende Meldungen (Cross-Marker)

- Board-Spalte zeigt Mini-OSM-Karte wenn `marker.lat` und `marker.lng` gesetzt
- Mobile Ansicht: Über das Phasen-Dropdown auswählbar (Wert `uebergreifend`)
- Foto-Zähler analog zu Site-Cards

## Suche

- Funkjournal: Client-seitige Suche über `data-fj-search` Attribut (Einheit, Kanal, Inhalt)
- Stab-Einsatzjournal: Client-seitige Textsuche in `.journal-row` Elementen
- Board: Existing `applyBoardFilters()` Funktion

## Pflicht: Zeitzonen (DB = UTC, Anzeige = Org-Zeitzone)

**Die DB speichert IMMER naive UTC. Niemals lokale Zeit in `DateTime`-Spalten schreiben.**

Die Anzeige-Zeitzone ist pro Org konfigurierbar (`FireDept.timezone`, IANA-Name, z.B.
`"Europe/Vienna"`). Fallback: `settings.DEFAULT_TIMEZONE = "Europe/Vienna"`.
Hilfsfunktionen: `app/core/timezones.py`. Jinja-Filter: `local`, `local_time`, `local_datetime`,
`local_iso` (lesen `user.org` aus dem Template-Context).

### Regeln

| Situation | Richtig | Falsch |
|---|---|---|
| Datetime im Template anzeigen | `{{ x\|local_datetime }}` oder `{{ (x\|local).strftime('...') }}` | `{{ x.strftime('...') }}` |
| `datetime-local`-Input vorbelegen | `value="{{ (x\|local).strftime('%Y-%m-%dT%H:%M') }}"` | `value="{{ x.strftime('%Y-%m-%dT%H:%M') }}"` |
| Datetime in Python ausgeben (PDF/CSV/XLSX) | `format_local_datetime(x, org)` | `str(x)` oder `x.strftime(...)` |
| Form-Input speichern | `local_input_to_utc(wert, org)` | direkt speichern |
| Datumsfilter (Query-Range) | `local_date_to_utc(von, org=org)` | `local_date_to_utc(von)` |

- **Reine `date`-Felder** (kein Zeitanteil, z.B. `flug.datum`, `wartung.faellig_am`) → keine Konvertierung.
- **JS-Konsum mit Z-Suffix** (`strftime('%Y-%m-%dT%H:%M:%SZ')`) → korrekt, JS rechnet selbst um.
- **Service-Funktionen**, die Datetimes ausgeben (PDF, XLSX), erhalten `org` als Parameter.

### Schnellcheck vor Commit

```
# Darf in Templates nicht vorkommen (außer date-only oder Z-Suffix für JS):
rg "\.strftime\(" app/templates

# Darf in uas_pdf.py nicht vorkommen:
rg "str\(.*_at" app/services/uas_pdf.py
```

### Referenz-Implementierung

Termin/Teilnahme (`app/routers/ui_termin.py` + `app/templates/termin/`) und
Incident-PDF (`app/services/pdf_service.py`) sind korrekte Muster.

---

## Neue Features – Checkliste

Beim Entwickeln neuer Features prüfen:
- [ ] Formulare nutzen HTMX und kein `location.reload()`
- [ ] Datenanzeige wird nach Absenden sofort aktualisiert
- [ ] WebSocket-Broadcast für Multi-User-Sync eingeplant
- [ ] Mobile Ansicht berücksichtigt (≤760px)
- [ ] CSRF-Token in allen POST-Formularen (`_csrf`)
- [ ] Alle Datetime-Ausgaben über `|local*` / `format_local_datetime(.., org)` – kein rohes `.strftime()` auf DB-Datetimes
