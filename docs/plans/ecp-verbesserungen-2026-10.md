# ECP-Verbesserungen 2026-10 – Umsetzungsdokumentation

Plan: Auftrag vom 2026-10-22 (ECP-01..07, GSL-01..07, APP-01/02, DOC-01, SEC-01..04).
Pro Block: ID / Status / Ursache / Loesung / Dateien / Datenbank / Tests / Hinweise.

## ECP-02 – Spalten-Umbenennen

| Feld | Inhalt |
|---|---|
| Status | Teilweise – Ursache des dauerhaften Verlusts nicht reproduziert |
| Ursache | Serverseitig sicher: `rename_column_endpoint` aendert nur `column.title`, alle Karten und Services nutzen `column_id`/`code`. Regressionstest (3x Umbenennen) zeigt keinen Verlust. Verdacht liegt im Client: Einzelspalten-Swap per OOB (`_swapColumn`), Tabs/Dropdown wurden nicht nachgezogen. |
| Loesung | `column_renamed` laedt die komplette Kanban-Struktur (`_swapKanban`) und zieht Tabs und Dropdown nach (`syncLaneTabs`). Neues Lesescript `scripts/spalten_diagnose.py` findet Karten ohne gueltige Spalte. |
| Dateien | `app/static/js/app.js`, `app/static/js/incident-board.js`, `scripts/spalten_diagnose.py`, `tests/test_incident_column_rename.py` |
| Datenbank | keine |
| Tests | `tests/test_incident_column_rename.py` (2), Gesamtlauf 5887 passed |
| Hinweise | Auf Prod `scripts/spalten_diagnose.py` ausfuehren. Tritt der Verlust weiter auf: betroffenen Einsatz mit Uhrzeit und Browser/Geraet melden, dann `IncidentChange` pruefen. |

## ECP-05 – LIS nur bei Statusaenderung

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Live-Test gegen LIS offen) |
| Ursache | `push_vehicle_status_to_lis` wurde bei jedem Status-POST und bei jedem Karten-Verschieben aufgerufen, ohne Vergleich. Kein Retry, kein Erfolgsprotokoll; der Poll konnte frisch gesetzte Status zurueckschreiben. |
| Loesung | Aufrufer senden nur bei geaendertem `unit_status`. Im Push zusaetzlich Schutz gegen identische Wiederholung (zuletzt erfolgreich gesendeter LIS-Prefix je Fahrzeug), 3 Versuche mit Backoff 1 s/3 s, Erfolg als `IncidentChange` `lis.status_pushed`. Der Poll respektiert ein 30-s-Schutzfenster fuer lokal gepushte Status und loescht den Merker, wenn er einen LIS-Status uebernimmt. |
| Dateien | `app/services/lis/lis_sync.py`, `app/routers/ui_incident.py`, `tests/test_lis_sync.py`, `tests/test_lis_status_route.py` |
| Datenbank | keine |
| Tests | 4 neue Tests in `test_lis_sync.py`, 1 Routentest |
| Hinweise | Merker liegt im Prozessspeicher (nach Neustart wird der naechste Status wieder gesendet). Bei mehreren Gunicorn-Workern hat jeder Worker eigenen Speicher; dadurch ist eine seltene Doppelsendung moeglich, aber nie eine verlorene Aenderung. |

## GSL-01 – Neue Einsaetze live im GSL-Board

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Test mit zwei echten Browsern/Tablets offen) |
| Ursache | Die automatischen Pfade (`POST /einsatz/neu` mit Trigger-Alarmart oder Uebernahme in laufende Lage, API-Alarm `/einsatz`) legten die Einsatzstelle an, sendeten aber nur `notify_gsl_created` und keinen `site_created`-Broadcast. Der API-Pfad `POST /lage/.../stelle` sendete `site_created` ohne `site_id`, die Karte ignorierte das. |
| Loesung | Nach dem Commit ruft jeder Pfad `gsl_broadcast.stelle_angelegt` auf (Board laedt per `sitePhaseChanged` komplett neu, daher keine Duplikate). `site_id` im API-Broadcast ergaenzt. Reconnect-Resync und manueller Refresh bleiben unveraendert. |
| Dateien | `app/routers/ui_incident.py`, `app/routers/api_v1.py`, `tests/test_gsl_site_created_broadcast.py` |
| Datenbank | keine (UNIQUE-Index auf `(major_incident_id, external_key)` bewusst nicht ergaenzt, solange Altduplikate ungeprueft sind) |
| Tests | 2 neue Tests, schlagen ohne Fix fehl |
| Hinweise | LIS/Mail/DiBOS legen keine Einsatzstellen an; dort ist nichts zu tun. |

## GSL-02 – Prioritaetsfarben Lagekarte

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Sichtpruefung am Geraet offen) |
| Ursache | Marker-Farben waren in vier Kopien hartkodiert und wichen von den Board-Farben ab (`#ef4444` statt `#d42225` usw.). Prioritaetsaenderungen (`site_prio_changed`) aktualisierten die Karte nicht, `site_created` ohne `site_id` wurde ignoriert, die Prioritaetslegende fehlte. |
| Loesung | Eine Funktion `site_map_color` und `SITE_PRIORITY_HEX` neben `SITE_PRIORITY_COLOR`, Werte identisch zu `--red/--orange/--yellow`. Karte reagiert auf `site_prio_changed`, Prioritaetslegende mit Buchstabe + Text (nicht nur Farbe). |
| Dateien | `app/models/major_incident.py`, `app/routers/ui_major_incident.py`, `app/templates/incident_major/karte.html`, `tests/test_gsl_prio_farben_karte.py` |
| Datenbank | keine |
| Tests | 8 neue Tests |
| Hinweise | Der Infoscreen (`ui_infoscreen_alarm.py`, `_GSL_PRIO_COLOR`) und die Druckvorlage (`druck.html`) haben eigene Paletten und wurden nicht angefasst. |

## SEC-01 / SEC-02 / SEC-04 – Ungueltige Geraetezugaenge (Server und Web-Layer)

| Feld | Inhalt |
|---|---|
| Status | SEC-01 erledigt, SEC-04 erledigt, SEC-02 Web-Layer erledigt (nativer Android-Teil siehe Android-PR, Geraetetest offen) |
| Ursache | Unbekannte, widerrufene oder durch eine PIN ersetzte Tokens sowie gesperrte Benutzer wurden an `/api/v1/device/*` und `/geraet-login` still mit 401 bzw. Redirect abgewiesen: kein Log, kein Grund, kein Zaehler. Die App konnte endgueltig ungueltige Zugaenge nicht von Netzwerkfehlern unterscheiden und pollte weiter. `get_duty_state` ordnete App-Version dem neuesten Token des Benutzers statt dem anfragenden Geraet zu. |
| Loesung | `device_auth_failure_service`: Aggregation je (Hash-Praefix, Grund, Kanal), gedrosselte Schreibzugriffe (erster Fehlversuch sofort, danach hoechstens einmal pro Minute), nur 10 Zeichen des SHA-256-Hashs, nie das Token. Gruende `unknown`, `revoked`, `user_inactive`, `session_revoked`; Kanaele App-API, Geraete-Login, Sitzung. Die 401-Antwort bleibt im Body unveraendert und traegt zusaetzlich `X-ECP-Auth-Error: device_revoked|device_unknown|user_inactive`. `native-bridge.js` stoppt bei diesen Codes Duty-Poll, GPS und FCM-Versuche und zeigt die geforderte Meldung mit Button „Erneut verbinden" (fuehrt in den vorhandenen PIN-Prozess `/geraet-login-pin`); Netzwerkfehler und 5xx bleiben temporaer. Geraetemerkmale (`X-ECP-Platform`, `X-ECP-OS-Version`, `X-ECP-Device-Model`) werden nur gespeichert, wenn die App sie sendet. Admin `/admin/geraete-login`: Filter Alle/Aktiv/Widerrufen/Auffaellig, Spalten App/Geraet und Fehlversuche, Tabelle der fehlgeschlagenen Zugriffe, Detailseite mit Fehlversuchen, Bestaetigung beim Reaktivieren. Fehlversuche mit unbekanntem Token sind nur fuer System-Admins sichtbar. |
| Dateien | `app/models/user.py`, `alembic/versions/0305_device_auth_failure.py`, `app/services/device_auth_failure_service.py`, `app/routers/device_api.py`, `app/routers/auth.py`, `app/main.py`, `app/routers/ui_admin.py`, `app/templates/admin/device_tokens.html`, `device_token_detail.html`, `_auth_failures.html`, `app/templates/login.html`, `app/static/js/native-bridge.js`, `tests/test_device_auth_failures.py` |
| Datenbank | Migration `0305`: Tabelle `device_auth_failure`, Spalten `device_token.last_failed_at`, `fail_count`, `platform`, `os_version`, `device_model` (alle nullable bzw. mit Default; rein additiv, bestehende Daten unveraendert) |
| Tests | 12 neue Tests (alle Gruende, Aggregation, kein Volltoken in DB und Log, Sitzung, Admin-Filter, Org-Isolation, Web-Layer) |
| Hinweise | Zaehler im Prozessspeicher: nach Neustart kann die letzte Minute fehlen. Das Token steht beim QR-Login (`/geraet-login?token=`) im Query-String und damit im Gunicorn-Access-Log; Hinweis fuer SEC-03. „Abgelaufen" gibt es im Datenmodell nicht (kein `expires_at`): ein Token ist aktiv, widerrufen oder durch eine neue PIN ersetzt (dann „unbekannt"). `revoke_device_token` sperrt weiterhin auch den Geraete-Benutzer; Entkopplung wurde nicht geaendert. |

## APP-01 / APP-02 – Pull-to-Refresh und Verbindungsverlust

| Feld | Inhalt |
|---|---|
| Status | Teilweise: Web-Layer und native App-Seite implementiert und gebaut (Debug-APK), Geraetetest offen |
| Ursache | Im Web-Layer gab es kein Pull-to-Refresh. Der Verbindungsstatus war nur auf Board/Lage/Einheit sichtbar, die globale WebSocket loeste nach Reconnect keinen Resync aus und legte bei jedem Reconnect ein weiteres, nie beendetes Ping-Intervall an (Leak). |
| Loesung | `ELNative.refresh()` loest `ec:resync` mit `source: pull` aus (Board, Lage, Einheit nutzen ihre vorhandenen Resync-Funktionen); Seiten ohne Handler werden neu geladen, ausser ein Formular hat ungespeicherte Eingaben (dann Hinweis). `ELNative.ptrBlocked()` sperrt die Geste bei Karte, Dialog/Overlay, Drag-and-Drop, Editoren, gescrolltem Container und Eingaben; die App liest den Zustand ueber `window.ECPtr`. Offline: Hinweis statt Aktualisierung. Neues `conn-status.js` (Banner „Keine Verbindung ... Stand HH:MM:SS" bzw. „Verbindung unterbrochen"), kurze WebSocket-Abrisse (< 8 s) bleiben unsichtbar, ein einziger Resync pro Wiederherstellung, kein Polling. Ping-Leak der globalen WebSocket behoben. |
| Dateien (ECP) | `app/static/js/native-bridge.js`, `conn-status.js`, `app.js`, `lage_board.js`, `einheit_modus.js`, `sw.js`, `app/templates/base.html`, `tests/js/native_bridge.test.mjs`, `tests/js/conn_status.test.mjs`, `tests/test_app_web_layer.py` |
| Dateien (Android) | `scripts/MainActivity.java` (SwipeRefreshLayout), `scripts/prepare-android.sh`; PR BattloXX/Einsatzcockpit-Android#62 |
| Datenbank | keine |
| Tests | 2 + 2 Node-Testdateien (6 Tests), 5 Python-Tests; Debug-APK baut lokal |
| Hinweise | Die Geste wird beim Beruehren (touchstart) fuer den naechsten Zug festgelegt. Nicht abgedeckt: Offline-Outbox-Konflikte bei abgelaufenem Token (siehe SEC-02: der Einheitenmodus meldet 401 weiterhin als Zugang beendet). |

## GSL-03 – Einheitenansicht auf Android-Tablets

| Feld | Inhalt |
|---|---|
| Status | Teilweise: Code-Ursachen behoben und getestet, Gerätetest auf dem Tablet offen (WebView-Version und Konsole des betroffenen Geräts sind unbekannt, die Ursache wurde nicht auf einem Gerät reproduziert) |
| Ursache | (1) `init()` startete das erste Laden, bevor die Outbox existierte: schlug der erste Abruf fehl, fehlte der lokale Zustands-Cache und die Seite blieb leer; ein Fehler in `createOutbox` hätte `init()` komplett abgebrochen. (2) `crypto.randomUUID` fehlt in WebViews < 92: jede Meldung scheiterte schon beim Erfassen. (3) Eine abgelaufene Anmeldung (401) wurde als Netzwerkfehler behandelt: keine Meldung, 30-s-Polling lief weiter. (4) Leere Seite ohne Hinweis, wenn weder Server noch Cache Daten liefern. |
| Lösung | Outbox wird vor dem ersten Laden erzeugt (Fehler dabei lassen die Ansicht nutzbar), `zufallsUuid()` mit `getRandomValues`-Fallback, Zustände `geladen`/`ladeFehler` mit sichtbarem Hinweis und „Erneut versuchen“, bei abgelaufener Anmeldung Hinweis „Zugangslink bzw. QR-Code erneut öffnen“ und keine weiteren automatischen Abfragen. Zugangslink, SMS, QR und A4-Druck pro Einheit existierten bereits (Ressourcenkarte, Tab Zugang). Neu sind die Schnellaktionen SMS, Link kopieren, QR anzeigen, QR drucken und Einheitenansicht öffnen direkt an der Einheitenkarte der Ressourcenübersicht; sie öffnen den Zugang-Tab und lösen dort die vorhandene Aktion aus (gleiche Berechtigung, Bestätigung bei aktiver Sitzung, Protokollierung). |
| Dateien | `app/static/js/einheit_modus.js`, `einheit_outbox.js`, `ressourcen_karte.js`, `app/templates/einheit/einheit.html`, `app/templates/incident_major/_kraefteuebersicht.html`, `_ressource_karte_zugang.html`, `ressourcen.html`, `tests/test_einheit_tablet_zugang.py`, `tests/js/einheit_outbox.test.mjs` |
| Datenbank | keine |
| Tests | 5 Python-Tests, 1 Node-Test; `npm run test:js` 70 grün |
| Hinweise | Für die Ursachenklärung am Tablet: WebView-Version (chrome://version bzw. Android System WebView) und Remote-Konsole prüfen. Der „Öffnen“-Link ist wie bisher nur für Administratoren verfügbar (Simulation `?sim=`). |

## ECP-07 – Objektinformationen in KI-Vorschlaegen

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Praxistest mit echtem KI-Modell und echten Objekten offen) |
| Ursache | `suggest_tasks`, `generate_lage_hints` und der Lagebild-Kontext bekamen nur Meldungstext, Stichwort und Adresse; verknuepfte Objekte (Gefahren, Hinweise, BMA, Zufahrt) flossen nirgends ein. |
| Loesung | Neuer Service `objekt_ki_kontext`: laedt nur **bestaetigt** verknuepfte, **freigegebene** Objekte (max. 3) und baut daraus einen begrenzten Textblock (Name, Nummer, Art, Adresse, Gefahren mit UN-/Gefahrnummer/Klasse/Detail, Hinweise, Zufahrt, BMZ/FBF/Schluesselsafe, Besonderheiten, hinterlegte Plaene). Keine Kontakte und keine Benachrichtigungsadressen. Der Block wird mit Regeln an Auftragsvorschlaege, Lagehinweise und Lagebild angehaengt („nur diese Fakten, nichts erfinden, Objektname nennen, ACHTUNG fuer Sicherheitskritisches“); ohne Objekt bleibt der Prompt unveraendert. Zusaetzlich werden Objektgefahren **deterministisch** (ohne KI) als erster Lagehinweis bzw. als erster Auftrag „ACHTUNG Objektgefahren: ...“ erzeugt, auch wenn das Modell ausfaellt oder sie auslaesst. Auftragskarten mit diesem Titel erscheinen rot als „Objektgefahr (Objektverwaltung)“. Die Hintergrundaufgaben laufen nach dem Objekt-Matching (Reihenfolge der BackgroundTasks), manuelle Neugenerierung liest den aktuellen Objektstand. |
| Dateien | `app/services/objekt_ki_kontext.py`, `ai_service.py`, `incident_service.py`, `app/routers/ui_incident.py`, `api_v1.py`, `app/templates/incident/_task_card.html`, `tests/test_objekt_ki_kontext.py` |
| Datenbank | keine |
| Tests | 9 neue Tests (Inhalt, Ausschluss unbestaetigter/nicht freigegebener Objekte, deterministische Hinweise, Prompt mit/ohne Objekt, Hintergrundlauf bei KI-Ausfall, Lagebild) |
| Hinweise | Wird ein Objekt erst nach der KI-Generierung verknuepft, wirkt es bei der naechsten Generierung (Button Neu erzeugen oder Alarmstufe aendern). Dokumentinhalte (OCR-Volltext) fliessen bewusst nicht ein, nur Dokumentnamen. |

## ECP-01 – Objektinformationen beim Erstellen

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Sichtpruefung am Geraet offen). Auslegung: siehe Hinweise |
| Ursache | Das Verknuepfen eines Objekts gab es nur in der Einsatzinfo bzw. im Board-Objektpanel, also erst nach dem Anlegen. Die Dialoge „Neuer Einsatz“, „Meldung anlegen“ und „Auftrag anlegen“ hatten keine Objektauswahl. |
| Loesung | Eine gemeinsame Komponente (`incident/_objekt_picker.html` + `objekt_picker.js`, Datenquelle `/api/uebernahme/objekte`, Suche nach Name, Vulgoname, Adresse und BMA-Nr.) in allen drei Dialogen. Die drei Routen nehmen `objekt_id` entgegen, pruefen Modul, Berechtigung (Einsatzleiter, Objektverwalter, Admins) und Mandant **vor** dem Anlegen und verknuepfen danach ueber denselben Service wie die Einsatzinfo (`objekt_service.objekt_verknuepfen_manuell`: bestaetigt/manuell, Audit, Objektgefahren-Meldungen, Kontakt-Benachrichtigung, Autodruck, Board-Reload). Die Einsatzinfo-Route nutzt jetzt denselben Service. Die Auswahl bleibt bis zu 10 Minuten in der Sitzung erhalten (Eingabefehler, erneutes Oeffnen). Ohne Berechtigung oder aktives Modul erscheint das Feld nicht. Bei „Neuer Einsatz“ entsteht die Verknuepfung vor dem automatischen Matching und damit vor den KI-Vorschlaegen (ECP-07). |
| Dateien | `app/services/objekt_service.py`, `app/routers/ui_incident.py`, `ui_objekt.py`, `ui_annotation.py`, `app/core/templating.py`, `app/templates/incident/_objekt_picker.html`, `index.html`, `incident/board.html`, `base.html`, `app/static/js/objekt_picker.js`, `tests/test_objekt_beim_anlegen.py`, `tests/js/objekt_picker.test.mjs` |
| Datenbank | keine |
| Tests | 7 Python-Tests (Verknuepfung bei Einsatz/Auftrag/Meldung, Gefahrenmeldung, fremdes Objekt → 404 ohne Anlage, fehlende Berechtigung → 403 ohne Anlage, Sichtbarkeit, API), 5 Node-Tests |
| Hinweise | Im Datenmodell hat nur der **Einsatz** eine Objektverknuepfung; Meldungen und Auftraege besitzen keine eigene (`Message.objekt_gefahr_id` ist nur fuer automatische Gefahrenmeldungen). Eine bei Meldung oder Auftrag gewaehlte Auswahl verknuepft daher das Objekt mit dem Einsatz (wie „Weiteres Objekt verknuepfen“ in der Einsatzinfo). Eine eigene Objektspalte je Meldung/Auftrag wurde bewusst nicht angelegt (keine Datenbankaenderung ohne Bedarf). Soll sie kuenftig gewuenscht sein, ist das eine eigene Erweiterung. |
