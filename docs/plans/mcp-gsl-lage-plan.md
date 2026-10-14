# MCP: GSL-Lage führen (Lagebild, Einsatzstellen, Journal, Meldungen)

Stand 2026-10-13. Ausgangslage: `app/mcp/tools/gsl.py` deckt nur die **Ressourcenkarte**
(Einheiten, Führer, Zugang, Personal, Ausstattung) und die **Disposition** ab
(`gsl_einheit_disponieren`, `gsl_auftrag_aendern`, `gsl_auftrag_zurueckziehen`,
`gsl_einsatz_abschliessen`, `gsl_einsatz_wiedereroeffnen`). Lücken heute:

- Es gibt **kein Tool, das `lage_id` oder `site_id` liefert.** Ein Connector muss die IDs
  vom Benutzer erfragen, weil Lagen und Einsatzstellen nicht gelistet werden können.
- Es gibt kein Lagebild: keine Phasen-Spalten, keine Führungshinweise wie
  „Erkundet – disponieren?“ oder „Lagemeldung überfällig“, keine Kräfteübersicht.
- Einsatzstellen können nicht angelegt, priorisiert oder einem Abschnitt zugeordnet werden.
  Es gibt auch keine Lagemeldung und keinen Log-Eintrag.
- Eine Funk-Statusmeldung einer Einheit kann nicht in Vertretung erfasst werden
  (Erkundet, in Arbeit, abgeschlossen).
- Lagejournal, Funkjournal, Bürgermeldungen und übergreifende Meldungen fehlen ganz.

Ziel: Ein Claude-Connector kann eine laufende GSL **mitführen**. Er beantwortet
„Was ist los?“, legt Einsatzstellen aus Anrufen, Mails oder Funk an, erfasst Statusmeldungen
und Lagemeldungen, pflegt die Journale und arbeitet Bürgermeldungen ab. Alles läuft über die
Services, die auch das Board nutzt, mit Live-Broadcast an alle Board-Clients.

## Grundsätze

- **Keine Fachlogik im MCP-Adapter.** Das gilt wie im Docstring von `gsl.py`. Wo die Logik
  heute im Router steckt, wird sie zuerst in einen Service verschoben, und Router und MCP
  rufen dann beide den Service:
  - `site_create`: Geocoding, KI-Prio, Auto-Abschnitt, Log, Audit
  - `lage_journal_add`
  - `meldung_annehmen`, `meldung_ablehnen` und `als-lageinfo`
  - `site_bearbeiten`, `site_prio` und `site_sektor`
  - die Board-Statusroute (`site_einheit_status_setzen`)
  - Cross-Marker anlegen und Status ändern

  Bestehende Router-Tests sichern dieses Refactoring ab.
- **Neue Dateien:**
  - `app/mcp/tools/gsl_lage.py` für die Tools. `gsl.py` bleibt bei Ressourcen und
    Disposition.
  - `app/services/gsl_mcp_service.py` für reine Lesefunktionen, die JSON aufbereiten.
  - Server-Wrapper in `app/mcp/server.py` wie bei den bestehenden `gsl_*`-Tools.
- **Broadcast:** Die Broadcast-Aufrufe aus Router und `gsl.py` wandern in einen gemeinsamen
  Helfer (`gsl_broadcast.py`):
  - `site_created`
  - `site:card_changed`
  - `site_phase_changed`
  - `einheit:changed`
  - `journal_updated`
  - `cross_marker:changed`
  - `notify_gsl_live`

  So kann der MCP-Pfad keinen Broadcast vergessen.
- **Rollen:**
  - `READ`, `EDIT` und `MANAGE` wie in `gsl.py`.
  - Lese-Tools für alle Lage-Rollen inklusive readonly.
  - Schreib-Tools brauchen `EDIT` und eine aktive Lage. Sie laufen über
    `_edit()` / `_manage()` aus `gsl.py`; diese Helfer werden in ein gemeinsames Modul
    verschoben.
- **Module:**
  - Jedes Tool hat einen eigenen `module_check` über `get_mi_features`: `funkjournal`,
    `meldungen`, `sektoren`, `uebergreifend` und `geraeteverleih`.
  - Für die Basis-Tools wird bei der Umsetzung geprüft, womit das Menü das GSL-Modul
    freischaltet.
- **Scoping:**
  - Nur `lage.org_id == context.org_id`. Das gilt auch für system_admin.
  - Wird eine `site_id`, `dispatch_id`, `report_id` oder `marker_id` nicht gefunden oder
    gehört sie nicht zur Lage, kommt immer dieselbe Meldung „… nicht gefunden.“
- **`lage_id` optional:** Fehlt sie, wird `get_active_lage(org_id)` genommen. Gibt es keine
  aktive Lage, kommt eine klare Fehlermeldung.
- **Format:**
  - Zeiten als ISO-UTC mit `Z` (`_json`).
  - Jede Antwort enthält einen `ui_link`: Board `/lage/{id}`, für Einsatzstellen
    `/lage/{id}?site={site_id}`. Den Deep-Link-Parameter bei der Umsetzung prüfen.
- **Audit:** `gsl.mcp.*` bei jeder Mutation. Quelle in Logs und Journal ist `mcp`.

### Datenschutz – ausgeschlossen

- Bürgermeldungen:
  - Ausgeschlossen sind `reporter_name`, `reporter_contact` und `source_ip`.
  - Ausgegeben werden nur `kontakt_vorhanden: bool` und `telefon_verifiziert: bool`.
- Autor:
  - Journal, Funkjournal und Site-Log haben keine `author_name`/`user_id`, nur
    `quelle` (board, tablet, funk, mcp, system).
  - Bei Führungsfunktionen bleibt der Name erlaubt, wie im Einsatz-Plan.
- Stab: Ausgegeben wird nur der Soll/Ist-Check pro Funktion (besetzt ja/nein), keine Namen.
  Stab-Zuweisung per MCP ist nicht vorgesehen.
- Nie ausgegeben werden:
  - `LageToken`, Melde-Token und QR-Login/PIN
  - `public_token` der Lage
  - Links aus `gk_zugang_service`
- Personal und Telefonnummern der Einheiten kommen weiterhin nur über die bestehenden
  `gsl_ressource_*`-Tools mit deren Regeln.

## Werkzeuge

### P1 – Lagebild lesen (nur lesend)

| Tool | Inhalt |
|---|---|
| `gsl_lagen_liste(status="aktiv", limit=25)` | status ∈ aktiv/beendet/alle. Je Lage: id, Bezeichnung, Status, Beginn/Ende, Übung und Zähler (Stellen offen/gesamt, Einheiten im Einsatz, offene Bürgermeldungen). |
| `gsl_lagebild(lage_id=None)` | **Eine Antwort für „Was ist los?“**. Inhalt siehe unten. |
| `gsl_einsatzstellen_liste(lage_id=None, phase=None, prio=None, abschnitt_id=None, suchtext="", inklusive_erledigte=False)` | Kartenzeile je Stelle, Felder siehe unten. Sortierung wie im Board. |
| `gsl_einsatzstelle_lesen(lage_id, site_id, bereiche=None)` | Bereiche: allgemein, auftraege, log, medien, material, meldungen. Inhalt siehe unten. |
| `gsl_kraefteuebersicht(lage_id=None)` | `resource_service.kraefteuebersicht` als JSON: Einheiten je Status/Abschnitt, Disposition, frei/gebunden. |
| `gsl_abschnitte(lage_id=None, mit_geometrie=False)` | Sektoren mit Leitung (Funktion), Stellen- und Einheitenzählern, optional GeoJSON. |
| `gsl_journal(lage_id=None, quellen=["lagejournal","funkjournal"], seit="", kategorie=None, nur_offen=False, limit=100)` | `LageJournalEntry` + `CommLogEntry` chronologisch, `body_html` als Klartext, Medien als Metadaten. `nur_offen` filtert das Funkjournal auf nicht erledigt. |
| `gsl_meldungen(lage_id=None, status="new")` | Bürgermeldungen ohne Personendaten: Ort, Text, Koordinaten, Foto vorhanden, verknüpfte Stelle. |
| `gsl_uebergreifend_liste(lage_id=None, status="aktiv")` | Cross-Marker mit Typ, Ort, Status und Log-Anzahl. |

Inhalt von `gsl_lagebild`:

- Lage-Kopf
- Zähler je Phase und Prio
- `fuehrungs_hinweise` (`gsl_fuehrung_service`), zum Beispiel:
  - erkundet, aber nicht disponiert
  - Lagemeldung überfällig
  - fertig gemeldet
- Kurzform der Kräfteübersicht: frei, im Einsatz, in Bereitstellung
- offene Stellen mit Prio 1–2
- offene Bürgermeldungen
- die letzten 15 Journal- und Funkeinträge

Felder je Zeile in `gsl_einsatzstellen_liste`:

- id, Bezeichnung, Einsatzgrund, Adresse, lat/lng
- Phase, Prio, Abschnitt
- Einheiten-Chips aus `get_einheiten_chips_for_sites`, ohne abgezogene
- `nur_erkundet`, `alle_fertig`
- Lagemeldung fällig um / überfällig
- Foto-Zähler

Inhalt von `gsl_einsatzstelle_lesen`:

- Dispositionen mit allen Feldern:
  - `dispatch_id`, `auftragsart` und `status`
  - die Zeitstempel `bestaetigt`, `vor_ort`, `beendet`, `withdrawn` und `abgezogen`
  - `reihenfolge`
  - Auftragstext
- Site-Log inklusive Lagemeldungen
- Medien als Metadaten
- Material vor Ort (Verleih)
- verknüpfte Bürgermeldungen

### P2 – Einsatzstellen führen (schreibend, EDIT)

| Tool | Verhalten |
|---|---|
| `gsl_einsatzstelle_anlegen(lage_id, bezeichnung, einsatzgrund="", ort="", strasse="", hausnr="", lat=None, lng=None, prio=None, abschnitt_id=None, trotz_duplikat=False)` | Neuer Service `stelle_anlegen`, gleicher Pfad wie das Board: Geocoding, KI-Prio nur ohne `prio`, Auto-Abschnitt nur ohne `abschnitt_id`. **Duplikatprüfung** nur auf offene Stellen mit gleicher Adresse (Straße, Hausnummer und Ort normalisiert, ohne Groß-/Kleinschreibung und Leerzeichen); keine Distanzprüfung. Bei einem Treffer wird nichts angelegt und die Kandidaten kommen zurück (`trotz_duplikat=True` legt trotzdem an). |
| `gsl_einsatzstelle_aendern(lage_id, site_id, felder)` | Erlaubte Felder: bezeichnung, einsatzgrund, ort, strasse, hausnr, lat, lng, prio, abschnitt_id. Läuft über die extrahierten Services aus `bearbeiten`/`prio`/`sektor`. Bei Adressänderung wird neu geocodiert. |
| `gsl_einsatzstelle_phase_setzen(lage_id, site_id, phase)` | Nur manuelle Phasen über `setze_site_phase`. `erledigt`/`abgebrochen` werden abgelehnt und auf `gsl_einsatz_abschliessen` verwiesen, damit die Automatik-Regeln greifen. |
| `gsl_einsatzstelle_eintrag(lage_id, site_id, text, art="info")` | Site-Log über `site_log_service.add_site_log`. |
| `gsl_lagemeldung_erfassen(lage_id, site_id, lage="", massnahmen="", nachforderung="", text="")` | `format_lagemeldung` + `register_lagemeldung`, setzt also den Timer zurück. Mindestens ein Feld ist nötig. |
| `gsl_einheit_status_melden(lage_id, dispatch_id, status, kommentar=None)` | Funkmeldung in Vertretung: `kontext_fuer_einheit(..., quelle="funk")` + `setze_einheit_status` wie auf der Board-Route. Erkundungssperre, Auto-Abzug (#562) und Phasenautomatik gelten also gleich. Konflikte kommen als verständlicher Text zurück. Audit und Journal vermerken `mcp`. |
| `gsl_auftrag_wiedereroeffnen(lage_id, dispatch_id)` | `oeffne_auftrag_wieder` und setzt `abgezogen_*` zurück. Schließt die Lücke zu `gsl_einsatz_wiedereroeffnen`. |

Bestehende Tools: Kleiner Fix in der Beschreibung von `gsl_auftrag_zurueckziehen`. Sie soll
ausdrücklich sagen, dass das Tool bei aktiven Aufträgen zurückzieht (mit Benachrichtigung)
und bei fertigen Aufträgen nur abzieht (ohne SMS).

### P3 – Journale, Bürgermeldungen, übergreifend (schreibend, EDIT)

| Tool | Verhalten |
|---|---|
| `gsl_journal_eintrag(lage_id, betreff, kategorie="sonstiges", text="", von="", an="", veranlassung="", site_id=None, einheit_id=None)` | Neuer Service `journal_eintrag_anlegen`, aus dem Router extrahiert. Der `text` wird als Klartext escaped und in `<p>` gesetzt, nie als rohes HTML übernommen. |
| `gsl_funkjournal_eintrag(lage_id, von, an, text, site_id=None)` / `gsl_funkjournal_erledigt(lage_id, entry_id)` | Über `funkjournal_service.add_comm_entry` bzw. `toggle_handled`. Erledigt ist idempotent: Das Tool setzt den Zustand, statt ihn umzuschalten. |
| `gsl_meldung_bearbeiten(lage_id, report_id, aktion, site_id=None, bezeichnung=None)` | aktion ∈ `annehmen` (neue Stelle oder bestehende `site_id`, Foto wird übernommen), `ablehnen`, `als_lageinfo`. Läuft über den neuen Service `buergermeldung_service`. |
| `gsl_uebergreifend_anlegen(...)` / `gsl_uebergreifend_aendern(lage_id, marker_id, felder)` | Cross-Marker inklusive Status und Log-Eintrag, über extrahierte Services. |

### P4 – Medien (lesend, Upload optional)

- `gsl_medium_herunterladen(lage_id, art, medium_id, inline=False)`:
  - art ∈ `stelle`, `journal`, `uebergreifend`, `meldung`
  - Neue Download-Arten im Signer von `mcp_download_service`
  - `lade_download` prüft bei jedem Abruf live, ob Benutzer, Rolle, Org und Zugehörigkeit
    zur Lage noch stimmen.
- Optional: `gsl_einsatzstelle_foto_upload_vorbereiten` / `_uebergeben`. Muster wie beim
  Objekt-Upload (`upload_router`), Ablage über `lage_media_service.speichere_site_foto`.

### Bewusst nicht enthalten

- Lage anlegen, beenden oder wiedereröffnen: zu große Wirkung für einen Connector.
- Stab zuweisen: Personendaten.
- Löschen (Journal, Medien, Einheiten).
- Sektor-Geometrie bearbeiten.
- QR-Login und Melde-Token.
- KI-Lagebericht und Pressemeldung: Sie lassen sich bei Bedarf als späteres P5 mit
  `generate_situation_brief` nachziehen.

## Tests

Neue Testdatei `tests/test_mcp_gsl_lage.py`, Fixtures wie in `test_mcp_gsl_erweiterung.py`.

**Pro Tool:**
- Happy Path
- Fremde Org und falsche Lage ergeben „nicht gefunden“.
- readonly darf lesen, aber nicht schreiben.
- Eine beendete Lage blockiert alle Schreib-Tools.
- Bei deaktiviertem Modul ist das Tool nicht verfügbar.

**Datenschutz-Test:** Der JSON-Dump aller Lese-Tools enthält keine der folgenden Angaben:
- Melder-Name, Melder-Kontakt, IP
- Token, Autorname, Stab-Namen

**Fachliche Gleichheit mit dem Board:**
- `gsl_einheit_status_melden` „abgeschlossen“ zieht automatisch ab, ohne die Version zu
  erhöhen.
- Abarbeitung während der Erkundung wird mit Konflikttext abgelehnt.
- Eine Lagemeldung setzt den Timer zurück.
- Die Duplikatprüfung bei `anlegen` greift.
- `annehmen` legt eine Stelle an und übernimmt das Foto.

**Broadcast:** Per Monkeypatch prüfen, dass jede Mutation die erwarteten Events sendet.

**Refactoring:** Die bestehenden Router-Tests (`test_gsl_*`, `test_major_incident*`)
bleiben unverändert grün.

## Pakete

Je Paket ein Codex-Auftrag mit eigenem PR, Merge nach grüner CI:

1. **P1 Lesen:** `gsl_mcp_service` und die Lese-Tools mit Tests. Kein Refactoring nötig,
   geringes Risiko, sofort nutzbar.
2. **P2a Service-Extraktion:** Anlegen, Bearbeiten, Prio, Sektor und Statusroute plus der
   gemeinsame Broadcast-Helfer. Reines Refactoring ohne Verhaltensänderung.
3. **P2b Schreib-Tools Einsatzstellen und Status.**
4. **P3:** Journal, Funkjournal, Bürgermeldungen (inklusive Service-Extraktion) und
   übergreifende Meldungen.
5. **P4:** Medien-Download, optional Foto-Upload.

Danach folgt ein Live-Test mit claude.ai in einer Übungslage. Ablauf:
Lagebild abfragen → Stelle aus Freitext anlegen → Einheit disponieren → Funkmeldung
„erkundet“ → Lagemeldung → abschließen. Am Board wird dabei geprüft, ob alles live ankommt.

## Entscheidungen (2026-10-14)

1. Bürgermeldungsfotos sind per MCP herunterladbar, wie im Board.
2. Die Duplikatprüfung beim Anlegen vergleicht nur die Adresse, nicht die Entfernung.
3. Eine Funkmeldung per MCP läuft technisch als `funk` mit denselben Regeln und wird als
   „Funk (MCP)“ angezeigt.
