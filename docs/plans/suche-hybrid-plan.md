# Zentrale Suche (Volltext → hybrid) – Umsetzungsplan

Stand 2026-10-20. Grundlage: Konzept „ECP – Gesamtkonzept hybride & semantische Suche (MariaDB)“
vom 20.10.2026. Dieser Plan ist die ADR aus „PR 0“ des Konzepts: Er gleicht das Konzept mit dem
Code ab und legt die Pakete fest, die nacheinander umgesetzt werden.

## 1. Befund aus dem Code (Abgleich mit dem Konzept)

| Thema | Befund | Folge für den Plan |
|---|---|---|
| DB-Version | Test- und Produktivsystem laufen auf **MariaDB 12.3.3**. Docker, E2E und CI wurden am 2026-10-20 auf `mariadb:12.3` umgestellt (PR #643). Fremdinstallationen können noch auf 10.11 laufen. | Volltext (InnoDB-FULLTEXT) ist die Basis. `VECTOR` (ab 11.7) bleibt trotzdem außerhalb der Alembic-Kette, damit ältere Installationen migrierbar bleiben. Die CI kann die Vektorstufe jetzt direkt gegen 12.3 testen. |
| Testdatenbank | pytest läuft auf **SQLite** (`Base.metadata.create_all`, je xdist-Worker eine Datei). | `MATCH … AGAINST` gibt es dort nicht. Die Textsuche braucht ein Backend je Dialekt: MariaDB FULLTEXT, SQLite `LIKE`. Echte FULLTEXT-Tests laufen als eigener MariaDB-Test im Migrationsjob. |
| Vorhandene Suchen | Alle fachlichen Suchen sind `LIKE`-basiert und modulbezogen: `katplan_suche_service.suche`, `objekt_pflege_schreiben_service.suche_objekte`/`filtere_objekte_nach_text`, Wasserstellen- und Straßensperren-Suche in den MCP-Tools. Eine globale Suche gibt es nicht. GSL-Board und Journale filtern clientseitig. | Keine Fachlogik neu bauen. Adapter und Live-Abfragen rufen die vorhandenen Sichtbarkeitsfunktionen auf (`nur_produktiv`, `katplan_zugriff.darf_lesen/sieht_vertraulich`, `sichtbare_plaene`, `sichtbare_quellen`, `visible_incidents_q`). |
| Mandanten | `app/core/tenant.py` filtert SELECTs fail-closed für Tabellen in `_TENANT_TABLE_NAMES`. Bulk-Update/-Delete wird **nicht** gefiltert. | Neue Suchtabellen mit `org_id` in `_TENANT_TABLE_NAMES` aufnehmen. Zusätzlich in jeder Query explizit `org_id` filtern. Der Worker setzt `set_tenant_context(db, None)` und filtert selbst. |
| Hintergrundjobs | Schleifen in `app/main.py` laufen nur beim Leader (`LeaderLock`). Muster für Lease/Retry: `app/services/alarm_outbox.py`. | Der Index-Worker wird eine weitere Leader-Schleife mit dem Claim/Lease-Muster der Alarm-Outbox. Keine neue Infrastruktur, kein Redis-Zwang. |
| Änderungserkennung | Es gibt keine zentrale Stelle, an der alle Fachservices Änderungen melden. | Ein `after_flush`-Listener auf der Session schreibt Outbox-Zeilen in **derselben Transaktion** (nur für Whitelist-Modelle). Ein periodischer Abgleich (Hash/`aktualisiert_am`) dient als Sicherheitsnetz. Keine Änderungen in Dutzenden Services. |
| OCR | Seitentexte stehen bereits in `objekt_dokument_seite.volltext` und `katplan_dokument_seite.volltext`. | Kein neues Chunking von PDFs nötig: **eine Seite = ein Chunk** (Seitenbezug bleibt exakt). Lange Seiten werden in Abschnitte von etwa 2.500 Zeichen mit Überlappung geteilt und behalten die Seitenzahl. |
| Live-Daten | GSL (`IncidentSite`, `SiteLogEntry`, `LageJournalEntry`, `CommLogEntry`, `LageEinheit`), Einsätze, Straßensperren und Wasserstellen sind pro Lage bzw. Org klein und ändern sich laufend. | Diese Quellen werden **nicht indexiert**, sondern zur Suchzeit live per SQL abgefragt („strukturierte Anbieter“). Damit gibt es keinen veralteten Status. Der Index enthält nur ruhende Fachinhalte. |
| MCP | Registry `register_tool(...)` mit `required_roles` und `module_check`. Adapter in `server.py` ruft `_call_registered_tool()` auf. Die Doku schließt Personendaten in Einsatz-Tools aus. | Ein lesendes Tool `ecp_suchen` nutzt denselben Query-Service wie die UI. Kontakte bleiben komplett draußen. |
| Embeddings | Auf dem Server ist kein Embedding-Modell bekannt oder getestet. | Die semantische Stufe (Paket S8) bleibt gesperrt, bis Modell, Host und Dimension entschieden und gemessen sind. Alle Pakete davor funktionieren ohne Embeddings. |
| Konfiguration | Modul-Schalter liegen als `OrgSettings`-Spalten vor, plus Systemflag in `SystemSettings` (`_set_module_states`). | Statt einer eigenen Tabelle `search_index_settings`: `.env`-Flags plus Org-Schalter `suche_aktiv` in `OrgSettings`. |

### Abweichungen vom Konzept (bewusst)

1. **Kein Index für Live-Quellen.** GSL, Einsätze, Sperren und Wasserstellen werden immer live abgefragt. Das Konzept verlangt ohnehin die Live-Verifikation. Ein zusätzlicher Index brächte nur Veralterung und Personendaten-Risiko.
2. **Keine `search_index_settings`-Tabelle.** Konfiguration über `.env` und `OrgSettings`.
3. **Vektortabelle außerhalb von Alembic.** Sie wird erst in S8 per CLI/Worker idempotent angelegt, wenn Flag und DB-Fähigkeit vorliegen. Name `search_vector_<modellkennung>`. Die Alembic-Kette bleibt auf älteren Versionen lauffähig; in Test, Produktion und CI (12.3) ist die Fähigkeit immer vorhanden.
4. **Kein Cursor.** Ergebnisliste auf 50 begrenzt (Konzept erlaubt das für den MVP).
5. **Kontakte nicht in der universellen Suche** (weder UI noch MCP). Dafür gibt es die Kontaktsuche mit eigener Berechtigung.
6. **Historische Einsätze** zunächst nur live über `visible_incidents_q` (Stichwort, Adresse, Einsatzbericht-Text ohne Personen). Ein Index folgt nur bei Bedarf.

### FULLTEXT-Besonderheiten (MariaDB/InnoDB)

- `innodb_ft_min_token_size` ist standardmäßig 3. „BMA“ und „BMZ“ werden gefunden, „B1“ oder „F2“ nicht. Tokens unter 3 Zeichen sowie reine Zahlen laufen deshalb zusätzlich über die exakte Stufe (`LIKE` auf `title`/`search_keywords`).
- Die Standard-Stoppwortliste ist englisch. Für die Tabelle wird keine eigene Stoppwortliste verlangt. Die Abfrage läuft im `BOOLEAN MODE` mit `+wort*` je Suchwort (UND-Verknüpfung plus Präfix). So findet „Brandmelde“ auch „Brandmeldezentrale“. Sonderzeichen der Boolean-Syntax (`+-<>()~*"@`) werden vor der Abfrage entfernt.
- Deutsche Komposita („Brandmeldezentrale“ vs. „Zentrale“) findet FULLTEXT nur über das Präfix. Das Synonymlexikon (BMA↔Brandmeldeanlage, BMZ↔Brandmeldezentrale, FSD↔Feuerwehrschlüsseldepot, …) wird beim Indexieren in `search_keywords` expandiert. Es liegt als fest versionierte Python-Liste vor, nicht als LLM-Ausgabe.

## 2. Zielarchitektur

```text
UI /suche (global, Einsatz, GSL)      MCP ecp_suchen
              \                         /
          app/services/suche/abfrage.py  (eine Engine)
               |            |              |
       exakte Stufe   Volltext-Stufe   Live-Anbieter (GSL, Einsatz,
       (LIKE/IDs)     (MariaDB FT /     Sperren, Wasserstellen)
                       SQLite LIKE)
               \            |              /
                 RRF-Fusion + Deduplizierung
                            |
          Live-Resolver: Rechte + Status je Treffer neu prüfen
                            |
                 Treffer (Titel, Auszug, Fundstelle, URL)

Fachänderung -> after_flush -> search_outbox -> Leader-Worker ->
Adapter.dokument(id) -> Chunks + Hash -> search_document/search_chunk
```

### Module (neu)

- `app/models/suche.py`: `SearchDocument`, `SearchChunk`, `SearchOutbox`.
- `app/services/suche/`
  - `quellen.py`: Adapter-Protokoll und Registry. Pro Quelle: `quelle` (Kennung), `modelle` (welche ORM-Klassen Änderungen auslösen und wie man daraus die Quell-ID ableitet), `dokument(db, org_id, quell_id) -> IndexDokument | None` (None heißt löschen), `alle_ids(db, org_id)`, `pruefe_treffer(db, user, quell_id, scope) -> TrefferLink | None` (Live-Rechte, aktueller Status, URL).
  - `adapter_objekt.py`, `adapter_objekt_dokument.py`, `adapter_katplan.py`
  - `synonyme.py`: Synonymlexikon und Normalisierung (Umlaute, ß, Kleinschreibung, Telefonnummern entfernen).
  - `textsuche.py`: Dialekt-Backend (MariaDB FULLTEXT Boolean Mode; SQLite LIKE).
  - `indexer.py`: `indexiere(db, org_id, quelle, quell_id)`, `neuaufbau(db, org_id, quelle=None)`, Hash-Vergleich.
  - `outbox.py`: `after_flush`-Listener, `suche_outbox_loop()` (Leader), Claim/Lease/Retry/Dead-Letter, Abgleichslauf.
  - `live.py`: strukturierte Live-Anbieter.
  - `abfrage.py`: Planer, RRF, Ergebnisvertrag.
- `app/routers/ui_suche.py`, Templates unter `app/templates/suche/`.
- `app/mcp/tools/suche.py`.

### Datenmodell (Migration 0300)

- `search_document`: `id` BIGINT, `org_id` BIGINT NOT NULL (FK `fire_dept.id` ON DELETE CASCADE), `quelle` VARCHAR(40), `quell_id` VARCHAR(64), `scope_typ` VARCHAR(20) NOT NULL DEFAULT `'org'`, `scope_id` BIGINT NOT NULL DEFAULT 0, `titel` VARCHAR(255), `untertitel` VARCHAR(255) NULL, `search_keywords` TEXT NULL, `sichtbarkeit` VARCHAR(20) (`standard`/`vertraulich`), `lat`/`lng` FLOAT NULL, `meta_json` TEXT NULL (nur Whitelist: z. B. `plan_id`, `objekt_id`, `version_id`, `seiten_nr`), `inhalt_hash` CHAR(64), `quelle_geaendert_am` DATETIME NULL, `indexiert_am` DATETIME. Unique `(org_id, quelle, quell_id, scope_typ, scope_id)`, Index `(org_id, quelle)`. Kein `is_deleted`: Löschen heißt Zeile entfernen.
- `search_chunk`: `id`, `org_id` (redundant für Tenant-Filter und schnelles Vorfiltern), `document_id` (FK CASCADE), `chunk_nr`, `text` TEXT, `seite` INT NULL, `abschnitt` VARCHAR(255) NULL, `inhalt_hash` CHAR(64). Unique `(document_id, chunk_nr)`, Index `(org_id, document_id)`. Nur MariaDB/MySQL: `FULLTEXT ft_search_chunk (text)` und `FULLTEXT ft_search_document (titel, search_keywords)` per `op.execute`, Dialekt geprüft.
- `search_outbox`: `id`, `org_id`, `quelle`, `quell_id`, `status` (`pending`/`retry`/`done`/`failed`), `versuche`, `naechster_versuch_am`, `gesperrt_bis`, `letzter_fehler` VARCHAR(500), `erstellt_am`. Unique `(org_id, quelle, quell_id, status)` geht nicht sauber. Stattdessen Dedupe beim Einfügen (offene Zeile vorhanden → nichts tun) plus Idempotenz im Indexer (Hash).
- `org_settings.suche_aktiv` BOOLEAN NOT NULL DEFAULT 0.

`.env`/`config.py`: `SEARCH_ENABLED` (Default true; global aus = Route 404, Menü weg), `SEARCH_INDEX_WORKER_ENABLED` (true), `SEARCH_MCP_ENABLED` (true), `SEARCH_SEMANTIC_ENABLED` (false, erst S8).

### Indexierte Quellen (Whitelist, keine generische ORM-Serialisierung)

| Quelle | Inhalt | Ausgeschlossen | Sichtbarkeit / Live-Prüfung |
|---|---|---|---|
| `objekt` | Nummer, Name, Vulgoname, Kategorie, Adresse + Zusatzadressen, `informationen`, `anfahrtsweg`, Merkmale (Katalogtext + Freitext), Gefahren, BMA-Stammdaten ohne SMS/Mail-Ziele | Arbeitskopien (`entwurf_von_id`), Kontakte, `kontakt_info_*`, Pflegeaufträge, Status ≠ produktiv-sichtbar (nach Codeinspektion: dieselbe Bedingung wie die Objektliste für Leserollen) | Objektmodul aktiv + `_LESE_ROLLEN` aus `ui_objekt.py`; Objekt existiert, ist produktiv |
| `objekt_dokument_seite` | `titel`, `dokumentart`, `volltext` je Seite | Dokumente mit `freigabe_status != "freigegeben"` oder `ist_aktuelle_version = False` | wie Objekt + Dokument noch freigegeben/aktuell; URL auf bestehende geschützte Seitenansicht |
| `katplan_element` | freigegebene Version (`KatplanVersionElement` der `aktuelle_version_id`): Titel, Kennung, Typ, Beschreibung (Klartext), Ort/Straße | Arbeitsstand, archivierte Pläne | `darf_lesen`; `vertraulich` → `sichtbarkeit=vertraulich`, nur bei `sieht_vertraulich`; Version noch aktuell |
| `katplan_seite` | Quellseiten (`KatplanDokumentSeite.volltext`) der für Leser sichtbaren Quellen | nicht freigegebene Quellen | `sichtbare_quellen(db, plan, user)` |

Telefonnummern werden vor dem Indexieren aus allen Texten entfernt (eigene Erkennungs-Regex in `synonyme.py`; `app/core/telefon.py` hat nur Normalisierer, keine Erkennung im Fließtext). Das ist eine Datensparsamkeitsregel: Nummern sind über die Originalquelle weiter erreichbar.

### Live-Anbieter (nicht indexiert)

| Anbieter | Kontext | Abfrage | Rechte |
|---|---|---|---|
| `gsl_stelle` | GSL (`scope=gsl`) | `IncidentSite` der Lage: Adresse, Stichwort, Beschreibung, Phase, Priorität. Erledigte Stellen nur mit `inkl_erledigt` und dann mit Status-Kennzeichnung | wie GSL-Board-Lesezugriff (bestehende Lage-Abhängigkeit wiederverwenden) |
| `gsl_meldung` | GSL | `SiteLogEntry`/`LageJournalEntry`/`CommLogEntry`: nur Text, Zeit, Stelle. **Kein Autor, keine Person** | wie oben, Feature-Flags der Lage (`_feature("funkjournal")` usw.) |
| `gsl_einheit` | GSL | `LageEinheit`: Funkrufname, Status, aktuelle Stelle | wie oben |
| `einsatz` | global / Einsatz | `visible_incidents_q`: Stichwort, Adresse, Leitstellennummer (`lis_operation_number`), Zeitpunkt | bestehende Einsatz-Leserechte |
| `einsatz_meldung` | Einsatz (`scope=einsatz`) | Meldungen und Aufträge des Einsatzes, ohne Personen | `can_access_incident` |
| `strassensperre` | global / Einsatz / GSL | aktuell gültige Sperren per Text, im Einsatz zusätzlich nahe der Einsatzadresse (`active_closures_in_bbox`) | Straßensperren-Modul + Lesezugriff |
| `wasserstelle` | global / Einsatz / GSL | Text über Bezeichnung/Typ; mit Geo-Filter Distanz aus Koordinaten; `status` immer ausgeben (defekt deutlich) | wie Wasserstellen-Anzeige im Einsatz |

### Abfrage-Planer (deterministisch, ohne LLM)

1. Kontext prüfen: `global`, `einsatz` (+`scope_id`), `gsl` (+`scope_id`). Rechte serverseitig, unabhängig von der UI.
2. Klassifizieren: reine Zahl → Objektnummer, Leitstellennummer, Kennung (exakt). Muster `F\d{8}` oder ähnliche Leitstellenformate → Einsatz. Sonst freier Text.
3. Stufen ausführen, jeweils `limit*3` Kandidaten: exakt (LIKE/Gleichheit), Volltext (Index), Live-Anbieter passend zum Kontext.
4. RRF: `score = Σ 1/(60 + rang)` je Liste. Exakte Treffer stehen unabhängig vom Score oben.
5. Deduplizieren nach `(quelle, quell_id)`; mehrere Chunks eines Dokuments → bester Auszug, Fundstellen zusammenfassen.
6. Live-Resolver je Treffer (`pruefe_treffer`): verwirft nicht mehr berechtigte oder gelöschte Treffer und setzt Status und URL. Reichen die Treffer nach dem Filter nicht, werden einmal mehr Kandidaten nachgeladen (max. 2 Runden).
7. Antwort: `treffer[]` mit `quelle`, `quell_id`, `titel`, `auszug` (Klartext, Markierung erst im Template per `markiere()`), `fundstelle` (Seite/Abschnitt), `status`, `ist_aktuell`, `grund` (`exakt`/`volltext`/`live`/`semantisch`), `url`, `distanz_m`. Dazu `modi`, `teilweise` (bool), `index_stand` (ältestes offenes Outbox-Datum bzw. letzter Lauf), `hinweise[]`.
8. Fehler einer Stufe (Timeout, DB-Fehler) → `teilweise=true` und Hinweis. Nie stillschweigend „keine Treffer“.

Suchbegriffe werden nicht geloggt. Metriken nur aggregiert (Dauer, Trefferzahl, Stufen).

## 3. Pakete

Jedes Paket ist ein PR mit eigenen Tests. Codex-Aufträge nennen nur gezielte Testdateien. Kein `ruff format`, kein `--fix`, kein Umformatieren von Bestandscode.

### S1 – Datenmodell, Konfiguration, Textsuche-Backend
- Migration `0300_suche_index.py` (siehe Datenmodell), Modelle in `app/models/suche.py`, Import in `app/models/__init__.py`, Tabellen in `_TENANT_TABLE_NAMES`.
- `config.py` + `.env.example`: die vier Flags. `OrgSettings.suche_aktiv`.
- `app/services/suche/synonyme.py` (Normalisierung, Synonymexpansion, Telefonnummern-Entfernung) und `textsuche.py` (`volltext_kandidaten(db, org_id, woerter, quellen, limit) -> list[(chunk_id, document_id, rang)]`; MariaDB: `MATCH(text) AGAINST(:q IN BOOLEAN MODE)` gebunden, nie String-Konkatenation des Nutzertexts ins SQL; SQLite: LIKE je Wort auf `text`, `titel`, `search_keywords`).
- Tests: `tests/test_suche_textsuche.py` (Normalisierung, Boolean-Escaping, Synonyme, Telefonnummern raus, SQLite-Pfad, Tenant-Trennung).

### S2 – Adapter + Indexer + Neuaufbau-CLI
- Adapter `objekt`, `objekt_dokument_seite`, `katplan_element`, `katplan_seite` nach Whitelist oben. Chunking: strukturierte Datensätze 1–3 Chunks; Seiten je Seite, über 2.500 Zeichen geteilt (Überlappung 300).
- Jeder Chunk-Text beginnt mit dem Dokumenttitel (Zeile 1). Grund: Auf MariaDB matcht der Chunk-Zweig nur `search_chunk.text`. Sonst findet „BMZ Rattpack“ nichts, wenn „Rattpack“ nur im Titel und „BMZ“ nur im Seitentext steht (in S1 gegen MariaDB 10.11 beobachtet).
- `indexer.indexiere(...)`: Dokument aus Adapter, Hash-Vergleich, Chunks ersetzen; `None` → Dokument löschen. `neuaufbau(db, org_id, quelle=None)`.
- CLI in `app/cli.py`: `suche-neuaufbau --org <id>|--alle [--quelle …]`.
- Tests: `tests/test_suche_index.py` (Arbeitskopie nicht indexiert, nicht freigegebenes Dokument nicht, vertrauliches Katplan-Element markiert, keine Telefonnummern/Kontakte im Index, Hash verhindert Neuschreiben, Löschung entfernt Dokument, Tenant).

### S3 – Outbox + Worker + Abgleich
- `after_flush`-Listener: für neue/geänderte/gelöschte Instanzen der Whitelist-Modelle (inkl. Kindmodelle wie `ObjektMerkmal`, `ObjektGefahr`, `ObjektDokument`, `ObjektDokumentSeite`, `KatplanPlan.aktuelle_version_id`, `KatplanVersion`) die betroffene Quell-ID bestimmen und Outbox-Zeilen anlegen (Dedupe gegen offene Zeilen und `session.new`). Darf nie eine Fachtransaktion zum Scheitern bringen: Fehler loggen, nicht werfen.
- `suche_outbox_loop()` in die Leader-Schleifen von `app/main.py`; Claim/Lease wie `alarm_outbox._claim`; Backoff, nach 8 Versuchen `failed`; erledigte Zeilen nach 7 Tagen entfernen.
- Abgleich alle 30 min je Org mit `suche_aktiv`: fehlende/überzählige Dokumente erkennen (IDs aus `alle_ids` vs. Index) → Outbox.
- Freigabe-Entzug wirkt sofort über den Live-Resolver (S4), der Index zieht nach.
- Tests: `tests/test_suche_outbox.py` (Objekt ändern → Outbox → Worker-Durchlauf → Index aktuell; Dokument-Freigabe zurückgenommen → entfernt; doppelte Änderung → eine Zeile; Fehler im Adapter → retry/failed; Worker ohne `suche_aktiv` tut nichts).

### S4 – Abfrage-Engine + Live-Anbieter
- `live.py` mit den Anbietern oben, `abfrage.py` mit Planer, RRF, Resolver, Ergebnisvertrag (Pydantic: q 1–500 Zeichen, `limit` ≤ 50, erlaubte `typen`, `radius_m` ≤ 5.000, gültige Koordinaten).
- Tests: `tests/test_suche_abfrage.py`: exakte Objektnummer zuerst; GSL-Scope liefert nur Treffer dieser Lage; erledigte Stelle nur mit Schalter und gekennzeichnet; Sperre außerhalb des Gültigkeitszeitraums nicht „aktuell“; defekte Wasserstelle mit Status; Org A/B; Einsatz einer Kollaborations-Org sichtbar, fremder nicht; vertrauliches Element nur mit Rolle; Indextreffer, dessen Quelle inzwischen gelöscht oder nicht mehr freigegeben ist, erscheint nicht; Stufenfehler → `teilweise`.

### S5 – Oberfläche
- `GET /suche` (Seite), `POST /suche/treffer` (HTMX-Partial, CSRF; Suchbegriff nicht in URL/Access-Log), JSON `POST /api/suche` mit derselben Engine.
- Header-Lupe (Desktop + Mobil) und Tastenkürzel `/` bzw. Strg+K, nur wenn `SEARCH_ENABLED` und `suche_aktiv`. Filter-Chips nach Quelle. Treffer-Karte: Typ-Icon, Titel, Auszug mit `markiere()`, Fundstelle, Stand, Status-Badge, „Öffnen“, ggf. „Auf Karte“. Zustände „keine Treffer“ und „teilweise“ sichtbar unterscheiden.
- Einsatzkontext: Such-Modal im Einsatz (vorbelegt `scope=einsatz`), GSL: Suchfeld im Board-Kopf (`scope=gsl`), Schalter „Ganze Organisation“.
- Touch-Ziele ≥ 44px, ≤760px geprüft, Rückkehr behält Filter (Query-Status in `sessionStorage`).
- Tests: `tests/test_suche_ui.py` (Route-Rechte, Modul aus → 404, CSRF, Escaping von `<script>` in OCR-Text, Header-Link nur bei aktivem Schalter). `rg '[“”„‘’]' app/templates` sauber.

### S6 – MCP `ecp_suchen`
- `app/mcp/tools/suche.py`, `register_tool(name="ecp_suchen", required_roles=("readonly",), module_check=<suche_aktiv und SEARCH_MCP_ENABLED>)`, Adapter in `server.py`.
- Parameter wie API. Rückgabe knapp: `quelle`, `quell_id`, `titel`, `auszug` (max. 300 Zeichen), `fundstelle`, `status`, `stand`, `url`, plus Hinweis, welches Fach-Tool die Details liefert (`objekt_lesen`, `katplan_element_lesen`, `gsl_einsatzstelle_lesen`, …).
- Live-Anbieter im MCP ohne Personen: dieselbe Whitelist; Kontakte nie.
- Doku-Tabelle in `docs/wiki/Entwickler-MCP-Erweiterung.md` ergänzen.
- Tests: `tests/test_mcp_suche.py` (Rollenmatrix, Modul aus, Tenant, Ausgabefelder ohne Personen/Telefon, Parität zur API für dieselbe Anfrage).

### S7 – Betrieb, MariaDB-Test, Doku
- Admin-Seite (Org-Admin, unter Einstellungen): Schalter `suche_aktiv`, Indexstand je Quelle (Dokumente, letzte Indexierung, offene/fehlgeschlagene Outbox-Zeilen), Button „Neu aufbauen“ (Outbox-Füllung, nicht synchron).
- CI-Job „Migration (MariaDB)“: nach `alembic upgrade head` einen kleinen pytest-Lauf `tests/mariadb/test_suche_fulltext.py` (Marker, nur mit `MARIADB_TEST_URL`), der echte FULLTEXT-Treffer, Boolean-Escaping und `EXPLAIN` mit FULLTEXT-Index prüft.
- Wiki: Bedienhilfe „Suche“, Admin „Suchindex“, Entwickler-Abschnitt (neue Quelle anbinden), CHANGELOG „Unreleased“, `.env.example`.

### S8 – Semantische Stufe (gesperrt bis Entscheidung)
Vorbedingungen, die der Nutzer entscheidet:
- Embedding-Modell und Host (z. B. lokaler Ollama/TEI-Dienst auf dem Testserver), gemessene Dimension, Latenz und RAM.
- Testkorpus mit mindestens 100 realen Fragen und Soll-Treffern (anonymisiert), Basiswert mit FULLTEXT aus S7.

Umfang danach: `embedding_provider.py` (HTTP, Timeout 2 s, Batch, Circuit-Breaker), Fähigkeitsprüfung beim Start (`CREATE TABLE`-Probe mit `VECTOR` in einer temporären Tabelle statt Versionsvergleich), Tabelle `search_vector_<modell>` mit `VECTOR(n)` und `VECTOR INDEX … DISTANCE=cosine`, Backfill über die Outbox, Vektorstufe in RRF mit `limit*5` Kandidaten und Org-Filter, Evaluationsskript (Recall@10 FT vs. hybrid). Testsystem zuerst, Produktiv nur nach Messung.

### S9 – KI-Antworten (optional, separat)
Nicht Teil dieses Plans. Erst nach S8 und eigener Freigabe.

## 4. Offene Entscheidungen

1. Embedding-Modell und Host für S8 (blockiert nur S8).
2. Testfragen-Korpus für die Abnahme (Recall@10).
3. Ob Leserollen ohne Objektmodul-Rolle Objekttreffer im Einsatzkontext sehen dürfen (Annahme: gleiche Regel wie Objektanzeige im Einsatz).

## 5. Umsetzungsstand

| Paket | PR | Stand |
|---|---|---|
| S0 Plan/ADR | – | dieser Plan |
| S1 | #642 | umgesetzt: Migration 0300, Modelle, Flags, Textsuche; MariaDB-10.11-Probe lokal ok |
| S2 | #644 | umgesetzt: 4 Adapter, Indexer mit Hash, CLI `suche-neuaufbau` |
| S3 | (dieser PR) | umgesetzt: after_flush-Erfassung, Leader-Worker mit Lease/Retry, Auffächern, stündlicher Abgleich; MariaDB-12.3-Probe ok |
| S4 | – | offen |
| S5 | – | offen |
| S6 | – | offen |
| S7 | – | offen |
| S8 | – | gesperrt (Entscheidung) |
