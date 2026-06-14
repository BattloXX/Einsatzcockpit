# Einsatzleiter-Hilfswerkzeug – Entwicklungsregeln

## Stack

- **Backend**: FastAPI (Python), SQLAlchemy ORM, Jinja2 templates
- **Frontend**: HTMX + Alpine.js, Tailwind CSS (utility classes), Leaflet.js (maps)
- **Real-time**: WebSockets via `/ws/lage/{lage_id}` – broadcast mit `broadcast_lage()`
- **Sprache**: Deutsch (Österreich) – alle UI-Texte, Kommentare und Variablennamen

## Architektur

- Single-Tenant pro Org: Alle Lagen gehören zu einer `org_id` – keine Cross-Org-Queries
- Templates verwenden HTMX für Teilupdates und Formulare
- Board-Cards (`.site-card`) haben `data-site-id` Attribute für gezieltes HTMX-Swap
- WebSocket-Events steuern Live-Updates ohne Page-Reload

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

## Neue Features – Checkliste

Beim Entwickeln neuer Features prüfen:
- [ ] Formulare nutzen HTMX und kein `location.reload()`
- [ ] Datenanzeige wird nach Absenden sofort aktualisiert
- [ ] WebSocket-Broadcast für Multi-User-Sync eingeplant
- [ ] Mobile Ansicht berücksichtigt (≤760px)
- [ ] CSRF-Token in allen POST-Formularen (`_csrf`)
