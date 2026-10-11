# Plan: GSL – Abschnitte, Geräteverleih mobil, „+“-Button kontextabhängig

Stand 2026-10-11. Umsetzung durch Codex in drei PRs (A, B, C), danach Browser-Abnahme
(Desktop 1920/1280, Mobil 390) mit dem lokalen Playwright-Skript.

## Ausgangslage (geprüft)

- **Abschnitte** (`/lage/{id}/sektoren`, `incident_major/sektoren.html` + `_sektoren_grid.html`,
  Kontext `_sektoren_context` in `ui_major_incident.py:4078`): Je Abschnitt nur eine Liste von
  `site.bezeichnung` mit Prioritätspunkt. Man sieht nicht, um welche Einsatzstelle es sich handelt
  (keine Adresse/Einsatzgrund), nicht den Status (`SitePhase`: eingegangen … erledigt), nicht die
  disponierten Einheiten. Klick öffnet nichts. Erledigte Stellen stehen gleichrangig zwischen
  offenen.
- **Stellendetail** existiert als HTMX-Partial `GET /lage/{id}/stellen/{site_id}`
  (`_site_detail.html`) und wird auf dem Board und im Verleih bereits im Dialog
  `#siteDetailModal`/`#siteDetailContent` geöffnet (Muster in `verleih/list.html:116-122`).
- **Geräteverleih** (`/lage/{id}/verleih`, `verleih/list.html`): Der Button „+ Ausleihe“ steht im
  Block `lage_actions`. Der Block liegt in der Desktop-Reiterleiste von `_lage_layout.html:148`
  und ist mobil nicht sichtbar. Mobil kann man deshalb keine Ausleihe anlegen (Screenshot 1).
- **„+ Einsatz“ (Bottom-Nav, `base.html:317`)** ruft `openActiveBoardLane()` bzw.
  `neuenEinsatzOeffnen()` auf. In einer Lage öffnet er also den normalen Einsatz-Dialog statt
  eine Einsatzstelle in der GSL anzulegen. Der Dialog „Einsatzstelle anlegen“ (`#neuStelleDialog`,
  `POST /lage/{id}/stellen/neu`) existiert nur in `board.html:340ff`.
- **Überlappung mobil** (Screenshot 2): Die Lage-Leiste „[ÜBUNG] LAGE … Ausblenden“ überdeckt die
  Zeile „← Lagen / Lage T9 / Geräteverleih ▾“ teilweise.

## PR A – Abschnitte: Einsatzstellen klar, öffnenbar, Status sichtbar

1. **Kontext erweitern** (`_sektoren_context`): Stellen je Abschnitt sortiert nach
   offen vor erledigt, dann Priorität, dann Alter. Pro Stelle mitliefern:
   - Phase plus Label (`PHASE_LABELS`), Gruppe neu / in Arbeit / erledigt (`SITE_PHASE_GROUP_LABEL`)
   - Priorität plus Label
   - Adresse aus `strasse`/`hausnr`/`ort` und `einsatzgrund`
   - Zahl und Kurznamen der aktiven Einheiten (`dispatched_einheiten` ohne Rückzug, plus aktive
     `resources`)
   - nächste Lagemeldung fällig/überfällig (`naechste_lagemeldung_at`)

   Ohne N+1-Abfragen: Beziehungen mit `selectinload` laden.
2. **Darstellung** (`_sektoren_grid.html`). Jede Stelle wird eine Zeile/Karte mit:
   - Titel (`bezeichnung`)
   - Zweitzeile mit Adresse und Einsatzgrund
   - Status-Badge in den Farben des Boards (neu, in Arbeit, erledigt)
   - Prioritätspunkt mit Text (nicht nur Farbe)
   - Einheiten-Chips
   - Hinweis „Lagemeldung überfällig“

   Kopfzeile je Abschnitt mit Zählern „x neu · y in Arbeit · z erledigt“ statt einer Gesamtzahl.
   Erledigte Stellen sind standardmäßig eingeklappt („z erledigte anzeigen“). Leere Abschnitte
   zeigen weiter „Keine Einsatzstellen zugewiesen“. Die Stellen ohne Abschnitt („Pool“) bleiben
   unten.
3. **Öffnen**: Klick oder Enter auf eine Stelle lädt `GET /lage/{id}/stellen/{site_id}` in einen
   Dialog `#siteDetailModal` auf der Abschnittsseite (Muster aus `verleih/list.html`, inkl.
   `hx-on::after-swap` → `showModal`). Aktionen im Detail (Status, Disposition) funktionieren
   wie auf dem Board. Danach wird `#sektorenGrid` neu geladen, ohne F5. Dazu zusätzlich der Link
   „Auf dem Board zeigen“ (`/lage/{id}?openSite={site_id}`). Zugänglich als `<button>` bzw. `<a>`,
   nicht als `div`.
4. **Live**: Auf der Abschnittsseite den bestehenden Lage-WebSocket abonnieren (Muster der anderen
   Lage-Seiten). Bei `site:card_changed`, `site_created` und Zuordnungsänderungen nur
   `#sektorenGrid` per HTMX neu holen, entprellt.
5. **Mobil (≤760 px)**: einspaltig, Stellen-Zeilen mit min. 44 px Höhe, Badges umbrechen, keine
   horizontale Scrollbar.
6. **Tests**:
   - Kontext: Sortierung, Phasen-Zähler, erledigt eingeklappt, keine Cross-Org-Stellen
   - Grid-HTML enthält Status-Badge, Adresse und Detail-Link je Stelle
   - Detail-Route weiterhin mit Rollen- und Org-Prüfung
   - readonly sieht Stellen und Details, aber keine Bearbeiten-Knöpfe

## PR B – Geräteverleih mobil

1. **„+ Ausleihe“ mobil erreichbar**: Den Block `lage_actions` in `_lage_layout.html` auch mobil
   rendern (in der Lage-Kopfzeile oder als Zeile unter dem Mobil-Dropdown), ohne die Desktop-Leiste
   zu verändern. Zusätzlich die FAB-Umstellung aus PR C.
2. **Detail-Panel** (`.verleih-detail-panel`): mobil Vollbild-Sheet mit sichtbarem Kopf und
   Schließen-Knopf, über der Navigation (z-index-Lehre aus #511: Panel und Overlay über `.topnav`
   mit z-index 120). Formular `_ausleihe_form.html`: Felder volle Breite, Artikel-/Stücklisten-Suche
   und Mengenfelder mit Touch-Größen (≥ 44 px), kein Zoom-Sprung (Schriftgröße ≥ 16 px in Inputs),
   Speichern-Leiste unten sticky.
3. **Liste**: Filterchips scrollen horizontal statt umzubrechen, Karten einspaltig. Rückgabe und
   Teilrückgabe sowie der Druck des Verleihscheins sind mobil bedienbar.
4. **Überlappung Lage-Kopf (Screenshot 2)**: Die Lage-Leiste („[ÜBUNG] LAGE … Ausblenden“) darf
   die Kontextleiste nicht überdecken. Dazu Sticky- und z-index-Reihenfolge von `lage-ctx-bar` und
   Lage-Banner korrigieren und auf allen Lage-Seiten mobil prüfen.
5. **Tests**: Verleihseite rendert „+ Ausleihe“ auch im Mobil-Markup. Ausleihe anlegen und
   zurückgeben bleibt per HTMX ohne Reload. Bestehende Verleih-Tests bleiben grün.

## PR C – „+“-Button kontextabhängig

1. In `base.html` den Bottom-Nav-FAB über eine Template-Variable steuern, gesetzt per
   Kontextprozessor bzw. Block-Override in `_lage_layout.html`:
   - **Innerhalb einer Lage** (`/lage/{id}/…`, außer Verleih): Beschriftung „+ Einsatz“, Aktion
     „Einsatzstelle in dieser GSL anlegen“, also den Dialog `#neuStelleDialog` öffnen. Dafür den
     Dialog aus `board.html` in ein Partial (`_neue_stelle_dialog.html`) verschieben und in
     `_lage_layout.html` für alle Lage-Seiten einbinden, nur bei `can_edit`. Nach dem Anlegen
     aktualisiert die jeweilige Seite gezielt ihre Inhalte (Board: bestehender WS-Weg,
     Abschnitte: `#sektorenGrid`), kein Reload. Keine Übung-zu-echt-Verwechslung: Übungslagen
     legen Übungsstellen an (bestehende Logik von `stellen/neu`).
   - **Auf `/lage/{id}/verleih`**: Beschriftung „+ Verleih“, öffnet die neue Ausleihe
     (`hx-get /lage/{id}/verleih/neu` ins Detail-Panel).
   - **Außerhalb einer Lage**: unverändert (normaler Einsatz).
2. `aria-label` je Kontext anpassen. Rechte: ohne Bearbeitungsrecht in der Lage kein FAB bzw.
   Fallback wie bisher, statt eines toten Buttons. Der Einheitenmodus (`gsl_nur_lesen`) zeigt den
   FAB nicht.
3. **Tests**:
   - Lage-Seite rendert FAB „Einsatz“ mit Stellen-Dialog-Aktion
   - Verleih-Seite rendert „Verleih“
   - Seite außerhalb der Lage unverändert
   - readonly/Einheitenmodus ohne FAB
   - `POST /lage/{id}/stellen/neu` aus dem Partial funktioniert (CSRF, Org)

## Abnahme

- ruff, mypy, gezielte pytest-Läufe (sektoren, verleih, lage_layout, board), `npm run test:js`.
- Browser-Abnahme mit dem lokalen Playwright-Skript (SQLite und uvicorn, Chromium aus
  `~/.cache/ms-playwright`):
  - Abschnitte: Stelle öffnen, Status ändern, Liste aktualisiert sich
  - Verleih mobil: „+ Verleih“ anlegen und zurückgeben
  - In der Lage: „+ Einsatz“ legt eine Stelle an, die sofort im Board und in den Abschnitten
    erscheint
  - Keine Überlappungen bei 1920, 1280 und 390 px (Screenshots im PR beschreiben)
- CHANGELOG und Wiki (`Anwender-GSL-*`) aktualisieren.

## Entscheidung

- „+ Einsatz“ bietet in einer Lage **keinen** normalen Einsatz an – immer eine GSL-Einsatzstelle (Nutzer, 2026-10-11).
