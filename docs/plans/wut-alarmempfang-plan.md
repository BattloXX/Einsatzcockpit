# ECP – Alarmdaten über das W&T-Com-Gateway empfangen und interpretieren

Stand: 2026-10-26 (final, Entscheidungen eingearbeitet, Umsetzung noch nicht gestartet). Umsetzungsplan für Codex. Der Plan baut auf dem **bestehenden** ECPG-Alarmpfad auf
(`docs/plans/ecpg-gateway-plan.md`, PR6). Es wird keine parallele Alarmlogik gebaut.

## 0. Ausgangslage (aus dem Code geprüft)

| Baustein | Ist-Zustand | Fundstelle |
|---|---|---|
| Empfang W&T | Läuft **im ECPG-Gateway-Container** (separates Repo `BattloXX/einsatzcockpit-gateway`, lokal nicht ausgecheckt): TCP-Client auf den W&T-Com-Server (TCP-Socket-Server, Port 8000), Datagramm-Ende per Idle-Timeout oder Form Feed, Charset cp850/latin-1/utf-8, Ringpuffer der letzten 500 Rohdatagramme, optional Serial-Fan-Out ("Passthrough") | Wiki `Installation-Print-Alarm-Gateway.md`, `ui_gateway.gateway_wut_config` |
| Konfiguration | `Gateway.wut_config` (Host, Port, idle_ms, charset, datagram_strategy, passthrough_*), `Gateway.parser_config` | `app/models/gateway.py:267-272`, `gateway_service.py:222` |
| Status | WS-Nachrichten `serial_status` -> `Gateway.serial_connected`, `alarm_notice` (nur Log), `passthrough_status` | `app/routers/ws.py:853-880` |
| Ingest | `POST /api/v1/gateway/alarms` (Device-Token) -> `serial_alarm_service.ingest_alarm` -> `AlarmIngest` (`raw_hash` global UNIQUE) -> `create_incident` / `find_matching_incident` | `app/routers/gateway_api.py:71`, `app/services/serial_alarm_service.py` |
| Nachlauf | `enqueue_incident_alarm` (Alarm-Outbox), Autodruck (`alarm_serial_received`, `einsatz_created`), Geocoding, `match_incident_background` (Objekt), Broadcast | `gateway_api.py:100-160` |
| Parser | Liegt **im Gateway** (`RflVbgParser`, Regex aus `parser_config`); die Cloud bekommt `parsed` fertig geliefert und vertraut ihm. Dokumentierter Fehlfall: Ortsteil-/Rufnamen-Präfix ("bregenz VORKLOSTER ...", "_drehleiter r2 lauterach ...") liefert leere Adressfelder (siehe Kommentar in `lis_matching.py`, Vorfall 2026-07-14) | `lis_matching.py` Docstring |
| Leitstellennummer | `Incident.lis_operation_number` (UNIQUE je `primary_org_id`), Matching-Tier 2 in `find_matching_incident`. **Der serielle Pfad setzt sie nicht und übergibt sie nicht ans Matching.** | `incident.py:62-68,128`, `lis_matching.py` |
| EUS-Altkanal | EUS ruft `POST /api/v1/einsatz` (API-Key) und liefert `Leitstellennummer`, Dedup über `find_matching_incident` | `api_v1.py:560-720` |
| Protokoll | `IncidentLog` (Journal), `IncidentChange` (Feldänderungen, before/after) | `incident.py:609-642` |
| Migrationen | Head ist `0311`; neue Revision-IDs sind reine Nummern (`0312`, ...) | `alembic/versions/` |

### Lücken gegenüber der Aufgabenstellung

1. `raw_hash` ist **global** UNIQUE über den Rohtext: Ein später erneut gesendeter identischer Text (z. B. Probealarm, Wiederholung nach Tagen) wird verschluckt. Ein **geänderter** Text zur gleichen Leitstellennummer erzeugt dagegen einen neuen Einsatz, weil die Nummer nicht ins Matching eingeht.
2. Rohdaten werden erst gespeichert, wenn der Request ankommt, und dann **im selben Schritt** interpretiert und der Einsatz angelegt. Ein Fehler dort kostet den Ingest-Datensatz (Rollback). Empfang, Speicherung, Parser und Einsatzanlage sind nicht getrennt.
3. Kein Wiederverarbeiten, keine Versionen, kein Journal, kein Schutz manueller Korrekturen.
4. Der Parser lebt nur im Gateway; Änderungen brauchen Gateway-Updates, und die Cloud kann nichts neu auswerten.
5. Keine Diagnoseansicht in der Cloud (der Ringpuffer ist nur auf der lokalen Statusseite `:8631` sichtbar).
6. Orts-, Straßen- und Objektdaten der ECP werden im Parser nicht genutzt.

### Leitentscheidungen

- **Der Empfang bleibt im Gateway** (ausgehend zur Cloud, keine neuen Ports; das Gateway ist per Design die einzige Instanz mit Zugang zum W&T im LAN). Cloud-Worker halten keine W&T-Verbindung. "Nur eine aktive Instanz je Gateway-Verbindung" wird daher an zwei Stellen gelöst: (a) im Gateway-Container (Instanz-Lock, siehe P2), (b) in der Cloud über die bereits vorhandene WS-Presence (`agent_connection_id`) und DB-Idempotenz, sodass mehrere Uvicorn-Worker nie doppelt verarbeiten.
- **Die Cloud wird maßgebliche Parser-Instanz.** Das Gateway liefert ab jetzt immer `raw_bytes_b64` + `raw_text` + Metadaten; ein mitgelieferter `parsed`-Block wird nur als Vergleichswert gespeichert. Dadurch sind Parser-Updates und Wiederverarbeitung ohne Gateway-Release möglich.
- **Pipeline in vier getrennten Stufen** (jede einzeln testbar, jede darf die vorherige nie verlieren):
  `Empfang (Gateway)` -> `Speichern (AlarmIngest, committed)` -> `Parsen (alarm_parser, rein funktional)` -> `Einsatzanlage/Update (alarm_apply_service)`.
- Parser rein regelbasiert, keine KI-Abhängigkeit, kein Erfinden von Daten; Unklares landet unverändert in `rest_text`.
- Bestehende Kanäle (DIBOS, LIS, EUS-REST) bleiben unverändert funktionsfähig. EUS-REST wird nicht entfernt, sondern nur nicht mehr als Zielkanal ausgebaut; Dedup gegen den W&T-Kanal läuft über die Leitstellennummer.

## Gate 0 – Realdaten (vor jeder Parser-Zeile)

Ohne echte Mitschnitte wird nicht geraten. Zuerst klären (Mensch/Betrieb, nicht Codex):

1. Lokalen Klon des Gateway-Repos beschaffen (`gh repo clone BattloXX/einsatzcockpit-gateway ../einsatzcockpit-gateway`) und lesen: `serial_ingest.py`, `alarm_parser.py`, `spool.py`, Tests.
2. Produktiv-Gateway: `wut_config` (Host, Port, Modus, charset, datagram_strategy, idle_ms) per Admin-UI/DB notieren; am W&T bestätigen, dass der Port im **TCP-Server-Modus** läuft und wie viele gleichzeitige Verbindungen erlaubt sind (Gateway belegt eine; Passthrough/Fan-Out bleibt der Weg für weitere Clients).
3. Aus dem Gateway-Ringpuffer (`/` Statusseite `:8631`) **mindestens 30 reale Rohdatagramme als Bytes** exportieren (inkl. der drei Beispiele aus der Aufgabe, einer BMA, einer Nachbargemeinde, mehrerer Folgenachrichten zur gleichen Nummer). Anonymisieren (Namen, Telefonnummern) und als Fixtures unter `tests/data/alarm_wut/*.bin` + `*.expected.json` ablegen.
4. Aus den Bytes ableiten und im Plan-Anhang A festhalten: Charset, Nachrichtenende (Form Feed / CR LF-Folge / Idle), Steuerzeichen, ob Kopf-/Fußzeilen existieren, ob Folgenachrichten (Ergänzungen) vorkommen und wie sie sich markieren.

Wenn Mitschnitte fehlen, werden P1-P3 trotzdem gebaut (mit den drei Beispieltexten als Fixtures), aber **P4 (Update-Semantik) und die Endabnahme bleiben blockiert**.

## PR-Zuschnitt (sequenziell, jeweils eigener Branch/PR)

### P1 – Rohdatenspeicher und Pipeline-Trennung (Cloud)

Ziel: Nichts geht verloren; Anlage ist von Speicherung entkoppelt.

- Migration `0312`: `alarm_ingest` erweitern (alle Spalten nullable/Default, kein Datenverlust):
  `raw_bytes` (LargeBinary, Original-Bytes, Limit 64 KiB), `source` (String 20: `wut`/`manual`/`replay`), `received_at_gateway` (DateTime, vom Gateway gemessen), `sequence` (BigInteger, Gateway-Zähler), `process_status` (String 20: `stored`/`parsed`/`applied`/`needs_review`/`error`), `process_error` (Text), `parser_version` (String 20), `parsed_at`, `applied_at`, `leitstellennummer` (String 40, indexiert), `content_hash` (String 64).
  Eindeutigkeit **ersetzen**: `raw_hash` global UNIQUE entfällt, neu UNIQUE `(gateway_id, sequence)` (wenn Sequenz vorhanden) und Index `(org_id, leitstellennummer, content_hash)`. Backfill: `process_status='applied'` für Bestand, `content_hash=raw_hash`.
- `serial_alarm_service.ingest_alarm` in zwei Funktionen teilen:
  `store_raw(db, ...) -> AlarmIngest` (flush + **eigenes commit**, nur Insert, Duplikat-Erkennung nach `(gateway_id, sequence)` bzw. identischem `content_hash` binnen 10 min) und `process_ingest(db, ingest_id)` (Parser + Anlage, eigene Transaktion, Fehler -> `process_status='error'`, Rohdaten bleiben).
- `POST /api/v1/gateway/alarms` neu strukturiert: speichert, committet, antwortet sofort `202`-artig mit `{ingest_id, duplicate}`, verarbeitet danach inline (gleiche Request-Session, aber nach dem Commit) und fängt jede Exception ab. Antwortformat bleibt abwärtskompatibel (`ingest_id`, `duplicate`, `einsatz_id`, `parse_status`); alte Gateways ohne `raw_bytes_b64`/`sequence` funktionieren weiter.
- Nachlauf (Outbox, Autodruck, Geocoding, Objektmatching, Broadcast) aus dem Router in `alarm_apply_service.nachlauf(...)` verschieben, **Verhalten unverändert**; Reihenfolge und Hintergrundtasks wie bisher.
- Aufbewahrung (Entscheidung 3): `raw_bytes` 12 Monate, danach Nullsetzen durch einen täglichen Aufräum-Loop (Muster `ai_log_retention.py`, org-gefiltert, in Batches); `raw_text`, `parsed` und Journal bleiben erhalten.
- Wiederaufnahme: Loop `alarm_ingest_recovery` (Muster `alarm_outbox`/`dibos_loop`, mit Lease-Spalte oder `SELECT ... FOR UPDATE SKIP LOCKED`-Äquivalent über `process_status` + `lease_until`) verarbeitet `stored`/`error`(Versuche < 5) Datensätze, damit ein Absturz zwischen Speichern und Anlage nichts verliert. Nur ein Worker darf einen Datensatz gleichzeitig nehmen (atomares Update `stored -> processing` mit Rowcount-Prüfung).
- Tests: Speichern übersteht Parser-Exception; Absturz nach `store_raw` -> Recovery legt Einsatz an; alte Payload ohne neue Felder; zwei parallele Requests mit gleicher `(gateway_id, sequence)` -> ein Datensatz; Cross-Org-Isolation.

### P2 – Gateway: robuster Empfang (Repo `einsatzcockpit-gateway`)

Separates Repo, eigener PR; erst nach Gate 0 Schritt 1. Ziel: Bytes vollständig, mehrfach, wiederverbindend.

- Empfang als Byte-Strom mit Puffer: TCP-Pakete akkumulieren, Datagramme nach konfiguriertem Ende trennen (Form Feed / Idle / bei Gate-0-Ergebnis zusätzlich Trennmuster); **mehrere Nachrichten pro Verbindung** korrekt splitten; Rest im Puffer bleibt für das nächste Paket.
- Unvollständige Übertragung: Idle-Timeout schließt das Datagramm mit Flag `incomplete=true` ab (wird mit gesendet, nicht verworfen); Verbindungsabbruch mit Resttext im Puffer sendet diesen ebenfalls als `incomplete`.
- Reconnect mit exponentiellem Backoff (2 s bis 60 s, Jitter), TCP-Keepalive, `serial_status` bei jedem Wechsel, "letzter erfolgreicher Empfang" im Heartbeat.
- Instanz-Lock: Start-Lock im `ECPG_DATA_DIR` (Dateisperre) und Cloud-Einzigkeit über bestehende Presence; ein zweiter Container derselben Gateway-ID wird von der Cloud mit "ersetzt" beendet (vorhandenes Verhalten prüfen und testen).
- Jedes Datagramm sofort in den lokalen Spool/Ringpuffer **vor** jedem Parsen und vor dem Senden; Versand `POST /alarms` mit `raw_bytes_b64`, `raw_text` (dekodiert, `errors="replace"`), `charset`, `sequence` (monoton, im Spool persistiert), `received_at` (UTC), `source_addr`, `incomplete`. Nachsenden aus dem Spool bei Cloud-Ausfall (vorhanden, nur Felder ergänzen).
- Lokaler Parser bleibt als Notfall-Parser für den Offline-Notdruck, wird aber nicht mehr als maßgeblich gemeldet (`parsed` nur informativ).
- Tests mit `fake_comserver`: Datagramm in 3 TCP-Stücken, 2 Datagramme in einem Stück, Abbruch mitten im Datagramm, Reconnect nach Abbruch, Sequenz nach Neustart fortgesetzt.

### P3 – Strukturierter Parser (Cloud, rein funktional)

Neues Modul `app/services/alarm_parser/` (kein DB-Zugriff im Kern):

- `normalize.py`: Steuerzeichen/Form Feed entfernen, Whitespace normalisieren, Originaltext unverändert separat halten; Umlaut-Umschreibung der Leitstelle (`ae/oe/ue/ss` im Funkalarm) **nur zum Vergleichen**, nie im gespeicherten Originaltext.
- `tokenizer.py` + `rules.py`: reihenfolgeabhängiger Regelparser, Eingabe `ParserKontext` (Katalogdaten), Ausgabe `AlarmParseResult` (dataclass, JSON-serialisierbar).
- Felder (`AlarmParseResult`): `leitstellennummer` (Muster `^[a-z]\d{8}`, z. B. `f26008809`), `stichwort` (führend, z. B. `f2`, `f3`, `f11`), `weitere_codes` (z. B. `r1`, `r2`), `sonderkennzeichen` (z. B. `_drehleiter`), `gemeinde`, `ortsteil`, `strasse`, `hausnummer`, `adresszusatz` (z. B. "naehe hnr 11", "oestlich im wald"), `lagezusatz`, `meldebild` (Text nach `>`), `zusatzinfo` (weitere `>`-Segmente), `alarmzeit` (aus der Klammer `(TT.MM.JJJJ HH:MM Uhr)`, Org-Zeitzone -> naive UTC), `original_text`, `rest_text` (alles Nicht-Zugeordnete), `status` (`ok`/`teilweise`/`unklar`), `qualitaet` (0-100, Summe gewichteter Treffer), `hinweise` (Liste von Warnungen, z. B. `hausnummer_fehlt`, `gemeinde_mehrdeutig`), `parser_version`.
- Ortsdaten: `GemeindeKatalog` aus ECP (Gemeinden/Ortschaften der Org und Nachbargemeinden; vorhandene Quellen zuerst prüfen: `address_autocomplete.py`, `geo_service.py`, Planspiel-Ortschaften, Hydranten/Wasserstellen/Objekt-Adressen, OSM-Straßenimport) und `StrassenKatalog` je Gemeinde. Abgleich normalisiert (Umlaute, `str.`/`strasse`), Mehrdeutigkeit -> kein Raten, Hinweis `mehrdeutig` und Rohstück in `rest_text`.
- **Regelwerk als versionierte Daten (Voraussetzung für P7):** Der Parserkern enthält nur die Mechanik; alle anpassbaren Regeln liegen in einem `Regelsatz` (JSON-Schema, validiert mit Pydantic): Stichwort-Muster, Sonderkennzeichen (`_drehleiter`, ...), Präfix-/Rufnamen-Regeln, Gemeinde- und Ortsteil-Aliase, Straßen-Aliase/Schreibvarianten, Trennzeichen (`>`), Datumsformat, Reihenfolge-Varianten, Qualitätsgewichte. **Kein ausführbarer Code, keine frei eingebbaren Regex ohne Prüfung:** Muster werden beim Speichern gegen Länge (<= 200 Zeichen), verbotene Konstrukte (verschachtelte Quantoren, Rückreferenzen) und eine Laufzeitgrenze (Testlauf gegen 2 KiB Text <= 50 ms) validiert. Ein eingebauter Standard-Regelsatz (`version 0`, im Repo, Teil des Codes) ist immer verfügbar und dient als Fallback, wenn der aktive Regelsatz ungültig ist.
- Tabellen (Migration, nächste freie Nummer): `alarm_parser_regelsatz` (TenantScoped: `org_id`, `version` je Org fortlaufend, `status` `entwurf`/`aktiv`/`archiviert`, `regeln` JSON, `notiz`, `basis_version`, `erstellt_von_user_id`, `quelle` `ui`/`mcp`, `erstellt_am`, `aktiviert_am`, `aktiviert_von_user_id`; höchstens ein `aktiv` je Org per UNIQUE-Hilfsspalte oder Service-Prüfung) und `alarm_parser_testfall` (TenantScoped: `org_id`, `titel`, `rohtext` (anonymisiert), `erwartet` JSON, `ingest_id` null, `aktiv`, `quelle`, `erstellt_von_user_id`). `AlarmIngest.parser_version` verweist auf die Regelsatz-Version, mit der ausgewertet wurde (`0` = Standard).
- Auswahl zur Laufzeit: aktiver Regelsatz der Org (gecacht je Worker, Invalidierung über `version`-Abfrage im Minutentakt oder Broadcast), sonst Standard.
- Reihenfolge-Varianten (Gemeinde vor/nach Straße, Ortsteil-Präfix, Rufname/Sonderkennzeichen davor) per Regel-Tabelle; neue Varianten sind Datenzeilen/Testfälle, kein Codeumbau.
- Qualität/Status: `ok` nur, wenn Leitstellennummer + Stichwort + (Gemeinde oder Straße) + Alarmzeit gefunden und `rest_text` leer oder unbedeutend; sonst `teilweise`; ohne Leitstellennummer oder Stichwort `unklar`.
- Stichwort -> `alarm_type_code` über das bestehende Mapping (`map_stichwort` in `lis_mapping.py` und `lfv_stichworte_v03.py`); unbekanntes Stichwort -> bestehender Fallback `SONSTIGE`, Originalstichwort bleibt erhalten.
- BMA: Meldebild enthält BMA-Nummer/Melder/Objekt -> bestehende `finde_bma_nummern` (`objekt_matching_service`) wiederverwenden, Melder- und Objektangaben als `zusatzinfo` strukturieren (`bma_nummer`, `melder`, `melderbereich`).
- Parserdurchlauf `parse_ingest(db, ingest)` schreibt `parsed`, `parse_status`, `parser_version`, `leitstellennummer`; Fehler im Parser -> `process_status='needs_review'`, Rohtext bleibt.
- Tests: Parametrisierte Fixture-Tests aus `tests/data/alarm_wut/` (3 Beispiele aus der Aufgabe fest eingebaut: erwartete Felder wörtlich), dazu Normalalarm, mehrere Codes, Nachbargemeinde, fehlende Hausnummer, vertauschte Reihenfolge Gemeinde/Straße, BMA, Garbage-Eingaben (leer, nur Steuerzeichen, abgeschnitten); Property-Test "Parser wirft nie". Jeder Test prüft zusätzlich, dass `original_text` bytegenau erhalten bleibt und nichts im `rest_text` verschwindet, was als Feld erkannt wurde.

### P4 – Einsatzanlage, Updates, Schutz manueller Korrekturen (Cloud)

Neues `app/services/alarm_apply_service.py` (ersetzt `_create_or_link_incident`):

1. Schlüssel: `leitstellennummer` aus dem Parser. Fehlt sie (`unklar`), kein Einsatz-Auto-Update; Anlage nur nach bisherigem Fallback (Matching nach Stichwort/Adresse) oder als "unklassifizierter Alarm" mit Hinweis im Journal (heutiges Verhalten bleibt).
2. Suche: `find_matching_incident(..., lis_operation_number=nummer)` (Tier 2, bereits statusunabhängig) **zuerst**, danach die bestehenden Tiers. Anlage mit gesetzter `lis_operation_number` (Race-sicher: UNIQUE `(primary_org_id, lis_operation_number)`, `IntegrityError` -> Gewinner laden, wie in `api_v1.py:700` und `dibos_enrich`).
3. Nicht vorhanden: `create_incident` über die vorhandene Logik (Adresse, Meldung, Zeitpunkt, `reason`), `source="serial"`; Nachlauf wie heute.
4. Vorhanden: Nachricht als **Version** zuordnen: `AlarmIngest.einsatz_id` + `version_no` (je Einsatz fortlaufend, Migration `0313`: Spalte `version_no`, Tabelle `incident_alarm_field` s. u.). Identischer `content_hash` zur Nummer -> `dedup_action="duplicate"`, **keine** Änderung, kein Nachlauf, kein neuer Alarm.
5. Feld-Merge mit Herkunftsschutz: neue Tabelle `incident_alarm_field` (`incident_id`, `field`, `value`, `source` = `alarm`/`manual`, `ingest_id`, `updated_at`; UNIQUE `(incident_id, field)`). Regeln:
   - Feld wird bei Anlage mit `source=alarm` geführt.
   - Manuelle Änderung in der UI/API (vorhandene Änderungspfade, die `IncidentChange` schreiben) setzt `source=manual` -> Alarmdaten überschreiben dieses Feld **nie**; das Update wird stattdessen als Journalhinweis "abweichender Alarmwert" gezeigt.
   - Zusätze (Meldebild-Ergänzungen, neue Codes) werden angehängt, nicht ersetzt; `report_text` bekommt einen datierten Nachtragsblock statt Überschreiben.
   - **Stichwort-Erhöhung (Entscheidung 1: ja):** Ein höheres Alarmstichwort in einer Folgenachricht setzt `alarm_type_code` automatisch hoch. "Höher" ergibt sich aus der Rangfolge der vorhandenen Alarmtypen (Reihenfolge/Stufe aus `AlarmType` der Org, Fallback `lfv_stichworte_v03.py`); Herabstufen oder Wechsel zwischen nicht vergleichbaren Typen passiert nie automatisch, nur Journalhinweis. Ist `alarm_type_code` bereits `source=manual`, wird nicht überschrieben (nur Hinweis). Jede Änderung: `IncidentChange` + `IncidentLog`.
   - Adresse nur setzen, wenn vorher leer oder `source=alarm`.
6. Journal: je Änderung ein `IncidentLog`-Eintrag (`level=info`, Text "Alarmupdate v{n}: Meldebild ergänzt ..."), je Feld ein `IncidentChange` (`action="alarm.update"`, `api_key_id=None`, before/after). Broadcast `incident_updated` an Board/Infoscreen. **Erneute Alarmierung (Entscheidung 2: ja, bei neuen Codes):** Kommt ein bisher nicht gesehener Alarmcode (z. B. `r2`, `_drehleiter`) oder ein höheres Stichwort hinzu, wird über die vorhandene Alarm-Outbox (SMS/Teams/Mail/Push) nachalarmiert, mit Idempotenzschlüssel `(incident_id, code)`, damit derselbe Code nie zweimal auslöst. Reine Text-/Zusatzinfo-Änderungen alarmieren **nicht** erneut. Ausschlussbegriffe (`alarm_ausschluss`) und Übungs-Guard (`exercise_guard`) bleiben wirksam; Nachalarmierungen sind im Alarmtext als "Nachalarmierung" kenntlich.
7. Objektzuordnung: nach Anlage und nach jedem Adress-Update `match_incident_background` (vorhanden). Parser liefert Straße/Hausnummer/BMA-Nr. strukturiert, dadurch greifen die vorhandenen Tiers (BMA-Nr., Adresse, Geo). Gefahren-Meldungen (`erzeuge_gefahren_meldungen`), Hinweise und Einsatzdokumente des Objekts kommen unverändert über die vorhandene Objekt-Einsatz-Verknüpfung; der Plan fügt nur den Aufruf bei Adress-Updates hinzu. Manuell entfernte Objektzuordnungen werden nicht erneut angelegt (vorhandene `ObjektEinsatz`-Status prüfen).
8. Übungs-/Probealarm: Erkennung wie in `lis_mapping.is_exercise_label` (Parser setzt `ist_uebung`, wenn Leitstellentext es kennzeichnet).
- Tests: neu -> Einsatz mit `lis_operation_number`; identischer Text zweimal -> ein Einsatz, kein zweiter Alarm; geänderte Zusatzinfo -> Version 2, Journal, Feld-Merge, keine Doppelalarmierung; manuell korrigierte Adresse bleibt trotz neuer Nachricht; gleiche Nummer aus DIBOS vorher/nachher; EUS-REST mit gleicher Nummer verknüpft statt Duplikat; paralleler Insert derselben Nummer (IntegrityError-Pfad); Nachbargemeinde; Org-Isolation.

### P5 – Diagnosebereich und Wiederverarbeitung (Cloud-UI)

Route unter dem Gateway-Detail (Rolle `org_admin`/`admin`, `require_gateway_enabled`), Muster `ui_gateway.py` + `templates/gateway/`:

- Tab "Alarmempfang" (`/gateway/{id}/alarmempfang`):
  - Kopf: Verbindungsstatus (`serial_connected`, Gateway online, `last_seen_at`), **letzter erfolgreicher Empfang** (`MAX(received_at)`), letzte Leitstellennummer, Zähler heute: empfangen / verarbeitet / fehlerhaft / Duplikate.
  - Liste der letzten 200 `AlarmIngest` (Filter: Status `needs_review`/`error`/`incomplete`, Zeitraum, Nummer; HTMX-Partial, kein Reload), Zeitangaben über `|local_datetime_sec`.
  - Detail je Eintrag: Original (Text + Hex-Ansicht der `raw_bytes`), strukturierte Felder, Hinweise, Qualität, Versionen derselben Nummer, verknüpfter Einsatz, Verlauf der Verarbeitungsversuche.
  - Button **Neu verarbeiten** (POST, CSRF, nur `org_admin`/`admin`): führt Parser erneut aus und wendet auf einen bereits verknüpften Einsatz nur Felder mit `source=alarm` an (P4-Schutz greift identisch), schreibt Journal "manuell neu verarbeitet von {user}". Für Datensätze ohne Einsatz legt er den Einsatz an (idempotent über Nummer).
  - Button **Als geprüft markieren** für `needs_review`.
- Regelsatz-Verwaltung (gleicher Tab, Unterbereich "Parser"): Liste der Versionen mit Status, Diff Entwurf <-> aktiv, Testfall-Liste, **Probelauf** (Entwurf gegen die letzten N Rohdaten und alle Testfälle, Ergebnis als Diff: neu korrekt / unverändert / **Regression**), Button **Aktivieren** und **Auf Vorversion zurücksetzen**. Aktivieren ist nur in der Web-UI möglich (nur `org_admin`/`admin`, CSRF, Audit) und nur, wenn alle aktiven Testfälle bestehen und der Probelauf keine Regression gegenüber dem aktiven Satz zeigt (Override mit Bestätigung und Begründung im Audit). Die Aktivierung ändert nur die künftige Verarbeitung; bestehende Einsätze ändern sich nur über **Neu verarbeiten** (P4-Schutz).
- Live-Aktualisierung über vorhandenes `broadcast_org` (`alarm_ingest_changed`), WebSocket-Hook im Template wie andere Gateway-Tabs.
- Dienstüberwachung: `dienst_monitor_service` meldet bereits W&T-Verbindungen; ergänzen: Warnung bei vielen `needs_review`/`error` binnen 24 h (nur Anzeige, kein neuer Alarmkanal).
- Audit: Wiederverarbeitung über `write_audit`.
- Wiki: `docs/wiki/Administration-Print-Alarm-Gateway.md` um Abschnitt "Alarmempfang und Diagnose" ergänzen, Installationshinweis zu `incomplete` und Instanz-Lock.
- Tests: Rollenprüfung (Mitglied 403), Cross-Org (Org B sieht nichts von Org A, fremde Ingest-ID -> 404), Wiederverarbeitung idempotent, Filter, Hex-Ansicht bei Binärmüll ohne Exception.

### P7 – Admin-MCP: Rohdaten lesen und Parser optimieren (Cloud)

Ziel: Ein KI-Client (z. B. claude.ai über das vorhandene Remote-MCP) kann als Admin die Rohdaten und Parserergebnisse prüfen, Schwachstellen im Parser erkennen, Verbesserungen als **Regelsatz-Entwurf** vorschlagen und gegen reale Daten testen. **Die Aktivierung bleibt beim Menschen in der Web-UI.** Voraussetzungen: P3 (Regelsatz als Daten), P5 (Probelauf-Logik, UI-Aktivierung).

Einordnung in die MCP-Architektur (Details siehe `docs/wiki/Entwickler-MCP-Erweiterung.md`):

- Neues Tool-Modul `app/mcp/tools/alarm_parser.py`, Import und `@server.tool`-Adapter in `app/mcp/server.py` (Adapter rufen ausschließlich `_call_registered_tool()`).
- Berechtigung: `required_roles=("admin",)` (wie `benutzer_*`) bzw. gleichwertig zur Rolle der Gateway-UI (`org_admin`/`admin` - beim Bau gegen `has_role` prüfen, nicht breiter als die Web-UI). `module_check`: Gateway-Modul der Org aktiv (gleiche Quelle wie `request.state.gateway_enabled` in `app/core/dependencies.py`, als wiederverwendbare Funktion herausziehen) **und** MCP-Modul (`mcp_effective_enabled`, wird in `load_live_context` ohnehin geprüft). Ausgeblendet über `list_tools()` bei fehlendem Recht.
- Immer `context.org_id`-gescopt; kein Zugriff auf Rohdaten fremder Orgs; Gateway-Zuordnung über `AlarmIngest.org_id`.

Werkzeuge (Namenspräfix `alarm_`):

| Werkzeug | Wirkung | Art |
|---|---|---|
| `alarm_rohdaten_liste` | Rohdatensätze paginiert (max. 50/Seite) mit Filtern: `status` (`needs_review`/`error`/`incomplete`/`ok`), `qualitaet_max`, `von`/`bis`, `leitstellennummer`, `nur_ohne_einsatz`; liefert ID, Zeit, Status, Qualität, gekürzten Text, `truncated`-Flag | lesend |
| `alarm_rohdaten_lesen` | Ein Datensatz: Original-Text (maskiert, s. u.), Hex-Auszug der Bytes, Charset, Parserergebnis mit Feldern, Hinweisen, `rest_text`, Parser-/Regelsatz-Version, weitere Versionen derselben Leitstellennummer, verknüpfte Einsatz-ID | lesend |
| `alarm_parser_statistik` | Kennzahlen je Zeitraum: Anteil `ok`/`teilweise`/`unklar`, häufigste Hinweise, häufigste Tokens im `rest_text`, nicht erkannte Gemeinde-/Straßenfragmente (Kandidaten für Aliase) | lesend |
| `alarm_parser_regelsatz_lesen` | Aktiver Regelsatz, Entwürfe, Standard-Regelsatz, JSON-Schema der Regeln (damit der Client gültige Regeln erzeugen kann), Katalogauszug (Gemeinden, Ortsteile der Org) | lesend |
| `alarm_ortsdaten_pruefen` | Löst Fragmente gegen die ECP-Kataloge auf (Gemeinde, Ortsteil, Straße): Treffer, Mehrdeutigkeiten, ähnlichste Schreibweisen | lesend |
| `alarm_parser_entwurf_anlegen` / `alarm_parser_entwurf_aendern` | Neuer Entwurf (Kopie des aktiven Satzes) bzw. gezielte Änderung einzelner Regeln (`regel_aenderungen`: hinzufügen/ändern/entfernen); Schema- und Muster-Validierung wie P3; `quelle="mcp"`, Audit `alarm_parser.mcp_entwurf`; **kann nichts aktivieren**; max. 20 offene MCP-Entwürfe je Org | schreibt Entwurf |
| `alarm_parser_testen` | Probelauf eines Entwurfs (oder des aktiven Satzes) gegen eine Auswahl Rohdaten (IDs oder Filter, max. 200) und alle Testfälle; Rückgabe pro Datensatz Diff gegenüber aktivem Satz, Zusammenfassung mit Regressionen. **Rein lesend, ohne Einsatz- oder Ingest-Änderung, ohne Alarmierung/Outbox/Druck** (nutzt die reine Parserfunktion aus P3, nie `process_ingest`) | lesend |
| `alarm_parser_testfall_anlegen` | Testfall aus einem Rohdatensatz mit **erwartetem** Ergebnis (vom Menschen im Gespräch bestätigt); Text wird beim Speichern anonymisiert; Audit | schreibt Testfall |

Nicht per MCP möglich (bewusst, wie `SMS-Freigaben` bei Kontakten): Regelsatz **aktivieren/zurücksetzen**, Ingest **neu verarbeiten**, Einsätze ändern, Alarmierung auslösen, Rohdaten löschen. Der Client schlägt vor und testet, ein Admin gibt in der UI frei.

Datenschutz und Missbrauchsschutz (Rohalarmtexte können Namen und Telefonnummern von Notrufern enthalten, und die Antworten gehen an einen externen KI-Dienst):

- Ausgabe immer **maskiert**: Telefonnummern, E-Mail-Adressen, IBAN-ähnliche Muster und Muster "Notrufer/Melder/Anrufer + Name" werden ersetzt (`[Telefon]`, `[Name]`); die Maskierung gilt nur für die Ausgabe. Der Probelauf parst das Original; Texte und Parserfelder in der Antwort werden danach maskiert. Ein Parameter zum Abschalten der Maskierung existiert nicht.
- Hex-Ansicht und Rohtext nur im maskierten Umfang; `raw_bytes` werden nie komplett ausgeliefert (Auszug <= 512 Byte, Steuerzeichen sichtbar gemacht).
- Verbot von Personendaten wie in den Einsatz-Tools (`einsatz_mcp_service`): keine Anrufer-Felder, keine Mannschaftsnamen; Ausgabefelder nur per Whitelist.
- Jeder Lesezugriff schreibt einen Audit-Eintrag `alarm_ingest.mcp_gelesen` (Benutzer, Anzahl, Filter, ohne Inhalte). Mengenbegrenzung: max. 50 Datensätze je Aufruf, 300 Leseaufrufe je Stunde und Benutzer (bestehendes Rate-Limit-Muster der MCP-Schicht verwenden oder ergänzen).
- Testfälle speichern ausschließlich den maskierten/anonymisierten Text.
- Org-Admin kann das Werkzeugset separat abschalten: Org-Setting `alarm_parser_mcp_aktiv` (Standard **aus**), Schalter im Gateway-Detail; ohne Schalter sind die Tools ausgeblendet. Migration in derselben Revision wie die Regelsatz-Tabellen oder eigener nachgelagerter Nummer.

Dokumentation: `docs/wiki/Administration-MCP-Server.md` (neuer Abschnitt "Alarm-Parser optimieren" mit Arbeitsablauf: Statistik -> problematische Rohdaten lesen -> Entwurf -> Testen -> Testfälle ergänzen -> Freigabe in der UI), Tabellenzeile in `docs/wiki/Entwickler-MCP-Erweiterung.md` (Rollen/Modul-Check), Hinweis in `Administration-Print-Alarm-Gateway.md`.

Tests (`tests/test_mcp_alarm_parser.py`, Helfer `_mcp`, `_oauth_tokens`, `_rufe` aus `tests/test_mcp_fundament.py`): Rollenmatrix (Admin sichtbar/aufrufbar; `readonly`, `org_admin` ohne Admin-Recht, Einsatzleiter nicht), Org-Schalter aus -> Tools unsichtbar, MCP-Modul aus, Cross-Org (Rohdaten/Entwürfe/Testfälle fremder Org nicht lesbar), Maskierung (Telefonnummer/E-Mail/Notrufer-Name erscheinen in keiner Antwort, auch nicht im Hex-Auszug), Paging und Mengengrenzen, ungültiger Regelsatz (Schema, gefährliche Regex, zu lange Muster) -> `ValueError`, `alarm_parser_testen` ändert nichts (Zeilenzähler `Incident`, `AlarmIngest`, Outbox, `PrintJob` vor/nach identisch), Entwurf kann nicht aktiviert werden (kein Tool, UI-Route verlangt Session-Login, nicht Token), Audit-Einträge vorhanden, Entwurfslimit, Testfall-Anonymisierung.

### P6 – Abnahmetests (End-to-End) und Betriebsfreigabe

Pflichtfälle laut Aufgabenstellung, jeweils als Test: Normalalarm; mehrere Alarmcodes; Nachbargemeinde; fehlende Hausnummer; Gemeinde/Straße vertauscht; BMA mit Melder/Objekt; identische Nachricht erneut; geänderte Zusatzinfo zur gleichen Nummer; unvollständige Übertragung (`incomplete`); Gateway-Verbindungsabbruch (Spool-Nachsendung, Idempotenz, Sequenz); gleichzeitiger Betrieb mehrerer ECP-Worker (zwei parallele `process_ingest` auf demselben Datensatz -> genau ein Einsatz, genau eine Alarmierung; zwei Gateway-Verbindungen derselben ID -> eine bleibt aktiv). Dazu ein End-to-End-Test: `fake_comserver` -> Gateway -> Test-Cloud -> Einsatz mit Objekt und Gefahrenmeldung (dort, wo das Gateway-Repo CI bietet; sonst Cloud-seitig mit eingespielten Fixture-Payloads).

Betriebsfreigabe (manuell, nicht Codex): Gateway-Release mit P2 ausrollen, **Parallelbetrieb** mehrere Tage mit DIBOS/EUS-REST (Dedup über Leitstellennummer beobachten), danach erst Alarmierung auf W&T-Kanal als maßgeblich betrachten. Der EUS-REST-Kanal (`POST /api/v1/einsatz`) bleibt dauerhaft aktiv und wird nicht stillgelegt (Entscheidung 4); die Dedup-Tests W&T <-> EUS <-> DIBOS gehören deshalb zum festen Testbestand.

## Technische Leitplanken (verbindlich für alle PRs)

- Bestehende Alarmverarbeitung bleibt lauffähig: alte Gateways (ohne `raw_bytes_b64`/`sequence`) werden weiter akzeptiert; vor jedem PR die vorhandenen Tests `test_gateway_*`, `test_alarm_outbox`, `test_lis_matching`, `test_incident_duplicate_guard`, `test_print_triggers` grün.
- Keine KI in der Alarmverarbeitung (`ai_service`, `objekt_ki_service` nicht importieren). Der Admin-MCP (P7) ist ein **optionales Werkzeug für Menschen außerhalb der Kette**: er liest, testet und legt Entwürfe an, die Verarbeitung funktioniert ohne ihn identisch.
- Keine neuen externen Ports/Dienste: Empfang bleibt ausgehend im Gateway; Passthrough-Port bleibt unverändert.
- Tenant-Regeln: Gateway-Endpunkte laufen ohne Tenant-Kontext (`set_tenant_context(db, None)`), daher **jede** Query explizit nach `org_id` des Device-Tokens filtern; kein Bulk-`update()/delete()` auf Tenant-Tabellen; Diagnose-UI läuft mit Tenant-Filter.
- Zeit: DB naive UTC; Alarmzeit aus dem Text wird in Org-Zeitzone interpretiert und nach UTC umgerechnet; keine `strftime`-Ausgabe in Templates.
- Routen mit `Depends(get_db)` als `def`, Async nur über `run_async` (siehe `CLAUDE.md`); keine Smart Quotes; Icons nur über das Icon-System; keine Emojis in UI.
- Migrationen: `0312`, `0313`, ... nur Nummern, auf SQLite **und** MariaDB lauffähig (`batch_alter_table`, `server_default` bei NOT NULL); MariaDB-Fallen beachten (`IS`-Syntax, INT-vs-BigInteger bei FKs).
- Kein stilles `except: pass` (Ruff `S110`); Fehler loggen im Logger `einsatzleiter.alarm` und im Datensatz (`process_error`) festhalten.
- Vor jedem Merge: `ruff check app/`, `mypy app/ --ignore-missing-imports`, `pytest -q -n auto`; Gateway-Repo eigene CI.

## Reihenfolge und Abhängigkeiten

`Gate 0` -> `P1` (Cloud) -> `P3` (Cloud, kann parallel zu P2) -> `P4` -> `P5` -> `P7` (MCP) -> `P6`; `P2` (Gateway) parallel ab Gate 0 Schritt 1, Rollout vor der Betriebsfreigabe. P1 muss zuerst, weil P3-P5 darauf aufsetzen. P4 braucht P3; P5 braucht P1 und P4; P7 braucht P3 (Regelsatz als Daten) und P5 (Probelauf, Aktivierung in der UI).

## Getroffene Entscheidungen (2026-10-26)

1. Höheres Stichwort in Folgenachrichten erhöht das Einsatzstichwort automatisch (nie herabstufen, manuelle Werte geschützt).
2. Neue Alarmcodes/höheres Stichwort lösen eine Nachalarmierung über die Alarm-Outbox aus (idempotent je Code).
3. Rohbytes 12 Monate aufbewahren, danach nur noch Text.
4. EUS-REST bleibt dauerhaft aktiv, keine Stilllegung geplant.

## Status

Plan final; **Umsetzung noch nicht gestartet** (Start auf Anweisung). Gate 0 (Realdaten) ist Voraussetzung für P3 (Feinschliff), P4 und P6; P1 kann sofort beginnen. P7 (Admin-MCP) ergänzt am 2026-10-26: erst nach P3 und P5.

## Anhang A – Beispieltexte der Leitstelle (Parser-Fixtures)

```
f26008809 f2 wolfurt fruehlingstrasse naehe hnr 11 > brand container > notrufer nicht mehr vorort (06.10.2026 02:15 Uhr)
f26007798 f3,r1 buch koss oestlich im wald > beginnender waldbrand ca25 - 30m2 (03.09.2026 20:28 Uhr)
f26007737 f11 _drehleiter,r2 lauterach bad lerchenau 12 patientenrettung aus 1 og rettung vor ort (01.09.2026 19:31 Uhr)
```

Erwartete Zerlegung (Soll für die Tests): (1) Nr `f26008809`, Stichwort `f2`, Gemeinde Wolfurt, Straße Frühlingstraße, Adresszusatz "nähe hnr 11" (keine sichere Hausnummer), Meldebild "brand container", Zusatz "notrufer nicht mehr vorort", Zeit 06.10.2026 02:15; (2) Nr `f26007798`, Stichwort `f3`, Code `r1`, Gemeinde Buch, Lagezusatz "koss oestlich im wald" (Flur/Ortsteil nur, wenn der Katalog "Koss" kennt), Meldebild "beginnender waldbrand", Zusatz "ca25 - 30m2"; (3) Nr `f26007737`, Stichwort `f11`, Sonderkennzeichen `_drehleiter`, Code `r2`, Gemeinde Lauterach, Objekt/Ortsbezeichnung "bad lerchenau", Hausnummer 12, Meldebild "patientenrettung aus 1 og", Zusatz "rettung vor ort". Wo die Katalogdaten die Zuordnung nicht eindeutig hergeben, ist `rest_text`/Hinweis das richtige Ergebnis, nicht eine Vermutung.
