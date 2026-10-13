# MCP: Einsatzdaten lesen (Normaleinsatz)

Ziel: Ein Claude-Connector kann einen sichtbaren Einsatz vollständig auslesen –
Stammdaten, Einheiten, Aufträge, Meldungen, Verlauf/Funkjournal, verknüpfte Objekte
inkl. Einsatzdokumente und Medien. **Nur lesend. Keine Personendaten.**

## Grundsätze

- Neues Modul `app/mcp/tools/einsatz.py`, Hilfsfunktionen in
  `app/services/einsatz_mcp_service.py` (reine Lesefunktionen, keine Mutation, kein Commit
  außer Audit beim Download).
- Rollen: `EINSATZ_LESE_ROLLEN = STRASSENSPERREN_LESE_ROLLEN` (alle Einsatzrollen inkl.
  readonly; `has_role` lässt admin/org_admin/system_admin ohnehin durch).
- Sichtbarkeit: eigener Einsatz (`primary_org_id == org_id`) oder kollaborierend über
  `IncidentOrg`. Auch für system_admin **nur** die Token-Org (kein orgübergreifendes Lesen).
  Nicht sichtbar → `ValueError("Einsatz nicht gefunden.")`.
- Zeiten als ISO-UTC mit `Z` (wie `_json` in `app/mcp/tools/gsl.py`).
- Jede Antwort hat `ui_link` auf das Board (`{public_base_url}/einsatz/{id}`).

### Datenschutz – ausgeschlossen

- Personen-Spalte komplett: `RescuedPerson`, `PersonMedia`, Spalten mit
  `column_kind == "rescued"` – weder Inhalt noch Medien.
- Atemschutz (`BreathingTroop`, `AtemschutzPruefung`).
- Mannschaftsnamen: `commander_*`, `fahrer_*`, `fahrer2_*` an `IncidentVehicle`; nur
  `kommandant_gesetzt: bool`.
- Anrufer (`caller_name`, `caller_phone`), `access_pin_hash`, Tokens (`alarm_token`,
  `auto_geojson_token`, `IncidentToken`), `ai_report_draft`, WordPress-Felder,
  `author_name`/`user_id` an Meldungen, Tagebuch und Funkjournal.
- Tagebucheinträge mit `entity_type` für Personen/Atemschutz werden ausgefiltert.
- Objekt-Kontakte: nur `kontakte_anzahl`, keine Namen/Erreichbarkeit;
  BMA-Benachrichtigungsziele nur als `*_gesetzt` (wie `objekt_lesen`).

Erlaubt bleiben Führungsfunktionen, die auf dem Board als Rolle erscheinen:
`einsatzleiter_name` und Abschnittsleiter-Name.

## Werkzeuge

| Tool | Inhalt |
|---|---|
| `einsaetze_liste(status="aktiv", von="", bis="", suchtext="", uebung=None, limit=25)` | status ∈ aktiv/abgeschlossen/alle; Kurzzeile je Einsatz (id, nummer, alarmstufe, stichwort, adresse, status, Zeiten, übung, eigen/kollaborierend, Zähler). Neueste zuerst, limit ≤ 100. |
| `einsatz_lesen(incident_id)` | Stammdaten, Zeitstempel, Meldebild (`reason`, `report_text`, DIBOS-Felder, LIS-Nummer), Koordinaten, Einsatzleiter, beteiligte Orgs, Abschnitte, Wachenstatus (DIBOS), Lagehinweise, Zähler. |
| `einsatz_einheiten(incident_id, inklusive_abgezogene=False)` | `IncidentVehicle` mit Stammfahrzeug (Name, Funkrufname, Typ, Org), Abschnitt, Status, Zeiten, km, Zähler zugeordneter Aufträge/Meldungen. Dazu `alarmierte_wachen`. |
| `einsatz_auftraege(incident_id, status="alle", einheit_id=None, abschnitt_id=None)` | `Task` inkl. Detail, Quelle, Fälligkeit/überfällig, Einheit, Abschnitt, Medienliste (Metadaten). |
| `einsatz_meldungen(incident_id, status="alle", einheit_id=None, abschnitt_id=None)` | `Message` analog, plus Bezug zur Objektgefahr (`objekt_gefahr_id`, Name). |
| `einsatz_verlauf(incident_id, quellen=["tagebuch","funkjournal"], seit="", limit=200)` | `IncidentLog` + `IncidentCommLog`, chronologisch zusammengeführt, ohne Autor. |
| `einsatz_objekte(incident_id, inklusive_vorschlaege=True)` | Verknüpfte Objekte (`ObjektEinsatz`: quelle, status, distanz) mit vollen Objektdaten wie `objekt_lesen` (ohne Kontakte) und Einsatzdokumenten (aktuelle, freigegebene Version, Seiten mit Dokumentart/Titel, `bei_einsatz_drucken`). Nur bei aktivem Objektmodul, sonst leere Liste + Hinweis. |
| `einsatz_lagebild(incident_id)` | Eine Antwort für „was ist los?“: Kurzstammdaten, aktive Einheiten, offene Aufträge, offene Meldungen, Objekte (kurz, mit Gefahren), letzte 20 Verlaufseinträge. |
| `einsatz_objekt_dokument_herunterladen(incident_id, dokument_id, seite=None, inline=False)` | Download-Link wie `objekt_dokument_herunterladen`, aber für Einsatzrollen und nur für Dokumente eines mit dem Einsatz verknüpften Objekts. |
| `einsatz_medium_herunterladen(incident_id, art, medium_id, inline=False)` | art ∈ auftrag/meldung; Download-Link für `TaskMedia`/`MessageMedia`. Nie `PersonMedia`. |

Download-Tokens: bestehender Signer in `app/services/mcp_download_service.py`, neue Arten
`einsatz_objekt` und `einsatz_medium` mit zusätzlichem Schlüssel `i` (incident_id) bzw.
`m` (auftrag/meldung). `lade_download` prüft live: Benutzer aktiv, Rollen, Einsatz für die Org
sichtbar, Dokument gehört zu verknüpftem Objekt / Medium zum Einsatz.

## Pakete (je ein Codex-Auftrag, ein PR mit getrennten Commits)

1. **P1 Kern:** Service + `einsaetze_liste`, `einsatz_lesen`, `einsatz_einheiten`,
   `einsatz_auftraege`, `einsatz_meldungen`, Server-Wrapper, Tests.
2. **P2 Verlauf/Objekte:** `einsatz_verlauf`, `einsatz_objekte`, `einsatz_lagebild`
   (Objekt-Payload aus `objekt_lesen` in gemeinsame Funktion auslagern), Tests.
3. **P3 Downloads + Doku:** beide Download-Tools inkl. `lade_download`, Tests, Wiki
   (`Anwender-MCP-Assistent`, `Administration-MCP-Server`, `Entwickler-MCP-Erweiterung`),
   CHANGELOG.
