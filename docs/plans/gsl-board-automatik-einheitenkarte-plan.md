# Plan: GSL – automatisches Einsatzboard, Einheitenkarte, mobile Einsatzführung

## Kontext

Das Konzept „ECP – GSL: Gesamtkonzept Einsatzboard, Einheitenansicht und mobile Einsatzführung“ verlangt drei Dinge:
- Das Board folgt automatisch den Rückmeldungen der Einheiten.
- Erkundung und Abarbeitung werden getrennt.
- „Fertig gemeldet“ (gemeldet von der Einheit) und „Abgeschlossen“ (entschieden von der Leitstelle) sind getrennte Phasen.
- Die Einheitenansicht bekommt eine Karte mit dem nächsten Auftrag und Navigation.
- Die Ansichten für Einheiten und Führung werden mobil besser bedienbar.

Grundsatz: Bestehendes erweitern. Es entsteht keine zweite Statusverwaltung und keine zweite Kartenverwaltung.

**Entscheidungen des Users (2026-10-11):**
- Abarbeitung: „Vor Ort“ **oder** „In Arbeit“ verschiebt die Karte nach In Arbeit. Das ist das heutige Verhalten.
- Drag & Drop einer Einheit auf eine Karte erzeugt in den Spalten Eingegangen und Erkundung eine Erkundung, sonst eine Disposition.
- Die Zwischenstatus Bestätigt, Anfahrt und Vor Ort bleiben als kleine Nebenaktionen. Die große Hauptaktion springt zum nächsten fachlichen Schritt.

## Ist-Stand (Bestandsprüfung)

**Phasen**
- `SitePhase` (`app/models/major_incident.py:117`) kennt bereits `eingegangen, erkundung, bewertet, disponiert, in_arbeit, erledigt, abgebrochen`.
- Die DB-Spalte ist `String(20)` ohne Check-Constraint, ein neuer Wert braucht also keine DDL.
- Labels stehen in `PHASE_LABELS` (`major_incident_service.py:24`), die Reihenfolge in `PHASE_ORDER` (`ui_major_incident.py:117`).
- Der zentrale Setter ist `setze_site_phase()` (`major_incident_service.py:35`). Er schreibt Site-Log und Audit und steuert den Lagemeldungs-Timer.

**Aufträge**
- `EinheitSiteDispatch` (`major_incident.py:676`) mit `einheit_status` (zugewiesen…abgeschlossen, nicht_durchfuehrbar), `reihenfolge`, `version` und `withdrawn_at`.
- **Es gibt keine Auftragsart**, Erkundung und Abarbeitung sind also nicht unterscheidbar.
- Übergänge: `ERLAUBTE_UEBERGAENGE` (`einheit_service.py:114`). Statuswechsel laufen über `setze_einheit_status` (`einheit_service.py:359`).

**Automatik heute**
- Nur `vor_ort`/`in_arbeit` setzt die Phase `in_arbeit` (`einheit_service.py:479`).
- Disponieren setzt die Phase nicht.
- Alle fertig ergibt nur den Karten-Hinweis „Einheiten fertig – abschließen?“ (`_site_card.html:87`).
- Erledigt wird nur manuell gesetzt.

**Mutationspfade**
- Board, `ui_major_incident.py`: `einheit-zuweisen` :913, `einheit-freigeben` :953, `einheit-disponieren` :1061, `auftrag` :1115, `wiedereroeffnen` :1153, `status` :1180, `einheit-vor-ort` :1224, `einheit-abziehen` :1301, `phase` :569.
- Tablet und GK: `ui_einheit.py:803`.
- MCP: `app/mcp/tools/gsl.py` :555, :584, :612.
- Teilung: `ressource_pflege_service.py:1257`.
- Services: `resource_service.dispatch_to_site` :529, `withdraw_from_site` :833, `oeffne_auftrag_wieder` :658.

**Einheitenansicht**
- `einheit/einheit.html` mit `einheit_modus.js`: Übersicht und Detail. Eine Mini-Leaflet-Karte gibt es nur im Detail.
- `GET /einheit/api/karte` (`ui_einheit.py:1303`) existiert, wird aber nicht genutzt.
- Navigation über `gmaps_url` (`einheit_service.py:301`). In der Einheitenansicht wird keine Geolocation verwendet.
- Offline-Outbox mit `client_uuid`-Idempotenz.
- **Lücke:** Abziehen und Freigeben senden kein `einheit:changed`. GK-Handys sehen einen Rückzug erst nach 30 s.

**Führung**
- `dashboard.html` (`ui_major_incident.py:2208`) hat Phasen- und Prio-Statistik, aber keine Aufmerksamkeitsliste.
- Überfällige Lagemeldungen liefert `IncidentSite.naechste_lagemeldung_at` bzw. `_sektoren_context` :4095.
- „Ohne Rückmeldung“: `resource_service.py:1039`.
- Karte: `karte.html` (Leaflet 1.1.1 vendored, `map-config.js` `addOsmTileLayer`, Tile-Proxy `/karten/osm-tile`). Ab ≤640 px wird das Panel zum Bottom-Sheet.
- Rechte: Rollen-Sets plus `_can_edit` und `_can_manage`. Es gibt keine Sektorrechte, und das bleibt so.
- Bug: Ein gespeicherter Board-Phase-Chip „uebergreifend“ wird nicht wiederhergestellt (`board.html:615`).

## Fachmodell

1. **Neue Spalte `EinheitSiteDispatch.auftragsart`** String(12), NOT NULL, Default `abarbeitung`, Werte `erkundung | abarbeitung`. Migration **0277** füllt den Bestand mit `abarbeitung`.
   Eine Einheit kann zur selben Site eine Erkundung **und** einen Abarbeitungsauftrag haben, je eine aktive Zeile pro Art. Die bisherige Duplikatprüfung „aktiver Dispatch je Einheit+Site“ wird daher um die Auftragsart erweitert.
2. **Status je Art** (`ERLAUBTE_UEBERGAENGE` je Art):
   - **Erkundung:** zugewiesen → bestaetigt/anfahrt/vor_ort → `abgeschlossen`, angezeigt als **„Erkundet“**. `in_arbeit` ist nicht erlaubt; `nicht_durchfuehrbar` bleibt erlaubt.
   - **Abarbeitung:** wie bisher. Von jedem offenen Status direkt nach `in_arbeit` ist erlaubt, ebenso von `in_arbeit` nach `abgeschlossen` („Fertig melden“).
   - Labels werden je Art aufgelöst über `einheit_status_label(art, status)`.
3. **Neue Phase `SitePhase.fertig_gemeldet`.** Das Label von `erledigt` wird **„Abgeschlossen“**; der Wert bleibt, damit keine Datenmigration nötig ist.
   Die Spaltenfolge ist dann Eingegangen · Erkundung · Bewertet · Disponiert · In Arbeit · Fertig gemeldet · Abgeschlossen (· Abgebrochen grau · Übergreifend). In `SITE_PHASE_GROUP` zählt `fertig_gemeldet` zur Gruppe `in_arbeit` (offen).
4. **Phasen-Automatik** im neuen Modul `app/services/gsl_phase_automatik.py`:
   - Einzige Funktion: `aktualisiere_site_phase(db, site, *, ausloeser, user=None, quelle) -> bool`.
   - Sie wird ereignisgesteuert **nach jeder Auftrags-Mutation** aufgerufen, nicht periodisch. Ein manueller Phasenwechsel bleibt damit bis zum nächsten Ereignis bestehen.
   - Sie nutzt ausschließlich `setze_site_phase` und erzeugt keine eigene Statushaltung.
   - **Regeln** (A = aktive Abarbeitungen, E = aktive Erkundungen; „aktiv“ heißt nicht zurückgezogen und nicht beendet):
     - Steht die Site auf `erledigt` oder `abgebrochen`, passiert **nichts**. Es gibt nie eine automatische Wiedereröffnung.
     - Ein A mit Status vor_ort oder in_arbeit ⇒ `in_arbeit`.
     - Sonst gibt es ein A ⇒ `disponiert`.
     - Sonst gibt es nicht zurückgezogene Abarbeitungen und alle sind beendet ⇒ `fertig_gemeldet`. Sind alle zurückgezogen, geht die Site ⇒ `bewertet`, falls je eine Erkundung existierte, sonst ändert sich nichts.
     - Sonst gibt es ein E ⇒ `erkundung`.
     - Sonst gibt es Erkundungen und alle sind beendet oder zurückgezogen ⇒ `bewertet`.
     - Sonst bleibt die Phase unverändert.
   - **Journal:** Eintrag über `resource_service._journal` mit neuen `ereignis_typ`-Werten (≤ 24 Zeichen): `erkundung_zugewiesen`, `erkundet`, `bewertet`, `alle_fertig`, `site_abgeschlossen`, `site_wiedereroeffnet`. Dazu kommen die vorhandenen `disponiert`, `begonnen`, `abgeschlossen`, `zurueckgezogen`, `prio`.
   - Den Auslöser (Person oder Einheit, `quelle`) übernimmt das Journal.
   - **Broadcast:** `site_phase_changed` + `notify_gsl_live(reason="counts")`, wie bei den bisherigen Phasenwechseln.
   - Der bisherige Sonderfall in `einheit_service.py:479` geht in diese Funktion auf.
5. **Aufruf in allen Mutationspfaden:**
   - zentral in `dispatch_to_site`, `withdraw_from_site`, `oeffne_auftrag_wieder` und `setze_einheit_status`
   - in den Board-Routen „vor-ort“ und „status“ (`set_vor_ort_at_site` setzt bisher keine Phase), sodass Board, Tablet, GK, MCP und Teilung automatisch abgedeckt sind
   - Ein Test prüft jeden Pfad.
6. **Einsatz abschließen / wieder öffnen:**
   - `POST /lage/{id}/stellen/{sid}/abschliessen`:
     - `_can_manage`
     - Phase ⇒ `erledigt`, Journal `site_abgeschlossen`
     - Bei noch aktiven Aufträgen: Rückfrage, danach werden sie zurückgezogen mit Grund „Einsatz abgeschlossen“.
   - `POST /lage/{id}/stellen/{sid}/wiedereroeffnen`: Phase ⇒ per Automatik neu berechnet, sonst `bewertet`, Journal `site_wiedereroeffnet`.
   - **Disponieren** und **Erkundung zuweisen** auf einer abgeschlossenen Site werden **abgelehnt** mit „Einsatz zuerst wieder öffnen“ (Board, MCP, Drag & Drop).
   - Der manuelle Phasen-Dropdown und Drag & Drop der Karte bleiben erhalten.
7. **Bestand:** Es gibt keine Rückwirkung auf laufende Lagen. Sites mit „alle fertig“ wechseln erst beim nächsten Ereignis.

## Phasen und PRs (Codex implementiert, Claude prüft, ergänzt Tests, committet)

Jede Phase hat einen eigenen Branch. Die Pakete darin sind getrennte Commits, pro Phase gibt es einen PR. Gemergt wird erst nach der Phasenprüfung und grüner CI; vor dem Merge kein `--auto`.

### Phase 1 – Statuslogik und Board (PR 1)

**P1.1 Modell, Automatik, Migration**
- `auftragsart`, Migration 0277 mit Migrationstest
- `fertig_gemeldet`, Labels
- `gsl_phase_automatik.py`, Status-Übergänge je Art
- Aufrufe in allen Pfaden (siehe 5.)
- Journal-Typen, `einheit_status_label`
- Abschließen und Wiedereröffnen als Service-Funktionen

**P1.2 Board-UI**
- `PHASE_ORDER`, `phase_colors`, Mobil-Chips, JS-Zähler (`board.html:551`, Liste offener Phasen) um `fertig_gemeldet` ergänzen
- In `_site_detail.html` bekommt das Formular „+ Disponieren“ (:514) zwei Schaltflächen: **„Erkundung zuweisen“** (primär in Eingegangen und Erkundung) und **„Disponieren“** (primär sonst).
- In der Dispatch-Zeile: Chip „Erkundung“ bzw. „Abarbeitung“; Statuslabel je Art
- Karte:
  - Chip „🔍 Erkundung“ an der Einheit
  - Hinweis „Einheiten fertig – abschließen?“ wird zur Schaltfläche **„Einsatz abschließen“** in Fertig gemeldet
- Drag & Drop (`einheit-zuweisen` :913): Art nach Spalte
- Fix für das Wiederherstellen von „uebergreifend“

**P1.3 Alle Phasen-Konsumenten**
- Per `rg "SitePhase\.|PHASE_LABELS|SITE_PHASE_GROUP|'erledigt'"` nachziehen:
  - Dashboard
  - Infoscreen (`ui_infoscreen_alarm.py:143`)
  - `gsl_live_service.py:24`
  - Pressetext und Bericht (`ui_major_incident.py:3685, 3929`)
  - Sektorenansicht
  - Phasenfilter der Karte (`karte.html:282`) und `_site_color`: `fertig_gemeldet` türkis
- MCP (`app/mcp/tools/gsl.py`):
  - `gsl_einheit_disponieren` mit Parameter `auftragsart` (Default `abarbeitung`)
  - Ausgaben zeigen Art und Phase
  - Ablehnung bei abgeschlossener Site
  - neue Tools `gsl_einsatz_abschliessen` / `gsl_einsatz_wiedereroeffnen` mit gleichen Rechten wie der Board-Abschluss, im MCP-Scope-Katalog ergänzt
- Texte in SMS, Push und Autodruck (`gk_zugang_service.auftrag_nachricht_rendern`, `gsl_einheit_push.py`, `gsl_einheit_autodruck`): Präfix „Erkundung:“ bei der Art erkundung

**Tests**
- Übergangstabelle der Automatik, alle 7 Auslöser
- mehrere Einheiten: Erkundung ×2, Abarbeitung ×2, gemischt
- Rückzug: letzte offene Erkundung bzw. Abarbeitung zurückgezogen
- Erkundung und Abarbeitung derselben Einheit an derselben Site
- keine automatische Wiedereröffnung nach erledigt
- Disponieren auf erledigt wird abgelehnt
- jeder Mutationspfad (Board, Tablet, GK, MCP, Teilung) löst die Automatik aus
- Journal-Einträge mit Auslöser
- Migration 0277
- MCP-Parameter
- Board rendert 7 Spalten, Smart-Quote-Check

### Phase 2 – Einheiten-API und Kartenansicht (PR 2)

**P2.1 API** (`einheit_service._auftragsdaten`, `auftraege_fuer_einheit`)

Neue Felder:
- `auftragsart`
- `naechster: bool`
- `rang`
- `hat_koordinaten`
- `nav_url`
- `geaendert_seit_gesehen`, berechnet clientseitig aus `version`

`naechster_auftrag` serverseitig:
1. aktueller laufender Auftrag
2. sonst erster offener nach `reihenfolge` (Vorgabe der Leitstelle)
3. dann `priority`
4. dann `dispatched_at`

Weitere Punkte:
- Der Rückzug sendet `einheit:changed` (Lücke in `ui_major_incident.py` ~954/1302 schließen); der Client zeigt ein Banner „Auftrag … wurde zurückgezogen“.
- `nav_url` = `https://www.google.com/maps/dir/?api=1&destination=lat,lng`. Ohne Koordinaten wird die Adresse als `destination` verwendet. Ist auch keine Adresse vorhanden, gibt es keinen Link.
- Prüfen, ob GK- und QR-Sitzungen den Tile-Proxy `/karten/osm-tile` laden dürfen; nötigenfalls für einen Einheiten-Principal freigeben, mit Cross-Org- und Rate-Test.

**P2.2 Einheitenkarte** (`einheit.html`, `einheit_modus.js`; neues Modul `app/static/js/einheit_karte.js`, versioniert über die Import-Map bzw. `ASSET_VERSION`)

- Leaflet über `addOsmTileLayer`. Die Daten kommen aus dem bereits geladenen `zustand`, keine zweite Datenquelle; offline zeigt sie den letzten Stand.
- **Marker:**
  - Nächster Auftrag: groß, mit Label „Nächster Auftrag“
  - Laufender Auftrag: „In Arbeit“
  - Weitere Aufträge: klein, nummeriert nach Rang
  - Erkundung: Lupe oder gestrichelter Rand
  - Fertige Aufträge: ausgeblendet, Schalter „Fertige einblenden“
  - Zurückgezogene Aufträge erscheinen nicht.
- **Verbindungslinie:** gestrichelt in Rangfolge, Legende „Reihenfolge (Luftlinie, keine Route)“. Keine Fahrzeiten.
- **Eigener Standort:**
  - nur nach Klick auf „📍 Mein Standort“ (`navigator.geolocation.watchPosition`, nur solange die Karte sichtbar ist)
  - wird **nicht** an den Server gesendet und nicht gespeichert
  - Fehler oder Ablehnung zeigen einen Hinweis, die Karte funktioniert weiter
  - Button zum Zentrieren
- **Vorschlag nach Entfernung** (nur mit Standort): sortiert nur innerhalb gleicher Priorität und nur Aufträge ohne `reihenfolge`, und nie vor einem laufenden Auftrag. Gekennzeichnet als „Vorschlag (Entfernung)“; Disposition und Priorität ändert er nicht. Umgesetzt als reine Funktion mit JS-Test.
- **Marker-Popup:**
  - Einsatzart und Kurztext, Adresse, Priorität, Art, Status, Hinweise
  - Schaltflächen „Auftrag öffnen“ und „Navigation starten“
- Liste ↔ Karte gegenseitig hervorgehoben (`data-dispatch-id`)
- **Ohne Koordinaten:** kein Marker. In der Liste steht „📍 keine Kartenposition – Adresse: …“, dazu ein Hinweiszähler auf der Karte.

### Phase 3 – Mobile Bedienung der Einheiten (PR 3)

- **Drei Bereiche Übersicht · Karte · Aufträge:**
  - Smartphone (≤ 760 px): Tab-Leiste unten, sticky, große Ziele ≥ 48 px
  - Tablet (≥ 1024 px oder Querformat): Liste und Karte nebeneinander, Übersicht oben
  - Der aktive Tab bleibt in `localStorage` (try/catch).
- **Übersicht:**
  - Einheit, Lage
  - aktueller Auftrag, nächster Auftrag
  - Anzahl offener Aufträge, Bearbeitungsstatus
  - „Öffnen“ und „Navigation“ direkt erreichbar
- **Aufträge:** Gruppen Aktuell / Nächster / Weitere offene / Fertig (eingeklappt) / Zurückgezogen (eingeklappt). Die Art ist als Chip sichtbar.
- **Aktionsleiste je Art** im Detail und in der Karte des aktuellen Auftrags:
  - Erkundung: Navigation · Lagemeldung · Foto · **Erkundung abschließen**
  - Abarbeitung: Navigation · **In Arbeit setzen** · Lagemeldung · Foto · **Fertig melden**
  - Die Hauptaktion springt direkt zum nächsten fachlichen Schritt. Bestätigen, Anfahrt und Vor Ort sind kleine Nebenaktionen.
  - Nicht zulässige Aktionen werden nicht angezeigt: bei Erkundung weder „In Arbeit“ noch „Fertig melden“, bei Abarbeitung kein „Erkundung abschließen“.
- **Foto** direkt per `capture="environment"` mit einem Tipp. Die Lagemeldung als Kurzformular im Bottom-Sheet. Darunter der Verlauf des Auftrags (die Chronik ist vorhanden).
- **Änderungen sichtbar machen:**
  - In der Liste bekommt ein Auftrag den Chip „GEÄNDERT“, wenn seine `version` höher ist als die zuletzt gesehene (in IndexedDB `meta`).
  - Banner bei Reihenfolgewechsel: „Die Leitstelle hat die Reihenfolge geändert – nächster Auftrag: …“.
  - Banner bei einem neuen Auftrag mit höherer Priorität als der laufende: rot, mit Ton.
  - Der laufende Auftrag wird nie automatisch verdrängt.
- **Verbindung:**
  - Die bestehende Outbox bleibt.
  - „Fertig melden“ und „Erkundung abschließen“ sind gesperrt, solange für den Auftrag eine gleichartige Statusmeldung aussteht. Der Status „⏳ nur auf diesem Gerät“ bleibt sichtbar.
  - Nach `online` und `visibilitychange` sofort neu laden.
  - Keine Erfolgsmeldung ohne Server-Bestätigung (das Verhalten gibt es schon; ein Test sichert es ab).

### Phase 4 – Mobile Führung (PR 4)

- **Dashboard** (`/lage/{id}/dashboard`) zur mobilen Führungsübersicht ausbauen:
  - Kacheln je Phase, inklusive Fertig gemeldet
  - Liste **„Braucht Aufmerksamkeit“**, wiederverwendbar aus einer neuen Service-Funktion `fuehrungs_hinweise(db, lage)`:
    - Priorität sofort oder dringend und nicht abgeschlossen
    - Lagemeldung überfällig (`naechste_lagemeldung_at`)
    - Auftrag nicht bestätigt seit mehr als X min (`zugewiesen` und `bestaetigt_at` leer; X = Lagemeldungsintervall der Org)
    - keine Rückmeldung (`resource_service.py:1039`)
    - Fertig gemeldet wartet auf Abschluss
  - Jede Zeile öffnet das Site-Detail.
  - Mobil einspaltig, Aktualisierung über WebSocket bzw. HTMX.
- **Site-Detail mobil** (`_site_detail.html`): sticky Aktionsleiste mit Erkundung zuweisen · Disponieren · Einsatz abschließen, gemäß `_can_edit` und `_can_manage`. Das Dialogfenster füllt bei ≤ 760 px den Bildschirm.
- **Karte** (`karte.html`):
  - Breakpoint auf ≤ 760 px angleichen
  - Filter „Nur Aufmerksamkeit“, gleiche Quelle wie das Dashboard
  - Umschalter Karte ↔ Übersicht (Links Board und Dashboard)
  - Prioritätsfarbe bleibt, Phase `fertig_gemeldet` sichtbar
  - Es entsteht keine neue Kartenverwaltung.
- Rechte unverändert, mit Test: readonly sieht die Hinweise, aber keine Aktionen.

### Phase 5 – Abnahme und Doku (PR 5, klein)

- **Playwright-E2E** (`tests/e2e`, Muster „Board ohne Full-Reload“), 390 px und 1280 px. Durchgespielt wird das Zielbild aus Kapitel 37:
  1. neue Site, Erkundung zuweisen
  2. die Einheit (GK-Session) sieht den Marker und schließt die Erkundung ab
  3. die Board-Karte geht auf Bewertet
  4. zwei Einheiten disponieren, die erste setzt In Arbeit
  5. beide melden fertig: Fertig gemeldet
  6. Einsatz abschließen
  7. kein Auto-Reopen
  
  Dazu: Kartenansicht ohne Geolocation-Freigabe und mit simulierter Freigabe, sowie Offline-Fertigmeldung ohne doppelte Meldung.
- **Wiki:**
  - `docs/wiki/Anwender-Grosschadenslage.md`: Spalten, Begriffstabelle aus Kapitel 31, Phasenchips statt Dropdown
  - `Anwender-Grosschadenslage-Karte.md`
  - `Anwender-GSL-Ressourcenverwaltung.md`: Einheitenkarte, Navigation, Standort-Datenschutz
- Plan nach `docs/plans/gsl-board-automatik-einheitenkarte-plan.md` kopieren.

## Regeln für Codex (je Paket)

- `--model gpt-5.6-terra --write`
- kein `ruff format` und kein `--fix`, Zeilen ≤ 120 Zeichen, ASCII-Quotes in Code und Attributen
- nur gezielte Tests; die volle Suite und die CI übernimmt Claude
- Codex committet nicht.
- Je Paket eine verbindliche Testliste, die Claude im Diff abhakt (Erfahrung: Codex liefert zu wenige Tests).
- Templates nach der CLAUDE.md-Checkliste:
  - mobil ≤ 760 px
  - `_csrf` in allen POST-Formularen
  - HTMX-Swap statt Reload, Broadcast
  - Zeiten über `|local*`
  - Tenant-Scoping bei Mutationen
- Neue Routen für den Einheiten-Principal bekommen einen Cross-Org-Test.

## Kritische Dateien

| Bereich | Dateien |
|---|---|
| Modelle, Migration | `app/models/major_incident.py`, `alembic/versions/0277_gsl_auftragsart.py` |
| Services | `major_incident_service.py`, `einheit_service.py`, `resource_service.py`, `ressource_pflege_service.py`, neu `gsl_phase_automatik.py`, `gsl_einheit_push.py`, `gk_zugang_service.py` |
| Router | `ui_major_incident.py`, `ui_einheit.py`, `ui_map_tiles.py`, `app/mcp/tools/gsl.py`, `ui_infoscreen_alarm.py`, `gsl_live_service.py` |
| Templates und JS | `incident_major/board.html`, `_site_card.html`, `_site_detail.html`, `dashboard.html`, `karte.html`, `einheit/einheit.html`, `_einheit_styles.html`, `static/js/einheit_modus.js`, neu `einheit_karte.js`, `lage_board.js` |
| Tests | `test_einheit_*`, `test_gsl_*`, `test_gk_*`, neu `test_gsl_phase_automatik.py`, `tests/js/einheit_karte.test.mjs`, E2E |

## Verifikation

1. Je Paket: `ruff check app/`, `mypy app/`, die gezielten pytest-Dateien, bei JS-Änderungen `npm run test:js`. Templates mit `rg '[“”„‘’]' app/templates`.
2. Je Phase: CI grün (MariaDB inklusive `alembic upgrade head` mit 0277, Playwright). Danach manuell mit Playwright bei 390 und 1280 px den Ablauf der Phase prüfen. Erst dann mergen.
3. Nach Phase 5:
   - E2E des Zielbilds grün
   - MCP-Tools gegen die lokale Instanz prüfen (disponieren mit `auftragsart`, abschließen)
   - Deploy auf test
   - Gerätetest am Tablet und am GK-Handy: Standortfreigabe ja/nein, Navigation öffnet Google Maps oder Apple Maps, Funkloch
