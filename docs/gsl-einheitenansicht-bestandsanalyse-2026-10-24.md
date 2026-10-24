# ECP – Phase 1 / Auftrag 1.1: Bestandsanalyse der GSL-Einheitenansicht

Stand: 2026-10-24 · Repository: `BattloXX/Einsatzcockpit` · untersuchte lokale Basis: `main`, Commit `495bff3dba7bac77bcac01b290ea2d8cd7f2dd9d` vom 2026-10-23.

Die Einheitenansicht unterstützt bereits beide Auftragsarten, Mehrfachzuweisungen, Rückmeldungen und Offline-Versand. Die größten Lücken liegen bei der Aktualisierung geöffneter Details, der Sicherung von Eingaben bei Speicherfehlern und der konsistenten Umsetzung von Zugangsrechten. Für erste Verbesserungen reichen Änderungen an bestehenden Templates, JavaScript-Modulen und API-Projektionen; ein neues Auftragsmodell oder eine Migration ist dafür nicht erforderlich.

Diese Analyse ändert ausschließlich Dokumentation. Befunde beruhen auf Quellcode, bestehenden Tests und gezielten lokalen Nachprüfungen mit synthetischen Testdaten. Ein aktueller Stand der GitHub-Remote oder einer produktiven Installation wurde nicht zusätzlich abgeglichen. Browserprüfungen erfolgten in Chromium; echte Smartphones, Tablets, Safari und Android-WebViews wurden nicht abgenommen.

## 1. Technischer Aufbau und konkrete Einstiegspunkte

Die Seite ist eine Jinja-Hülle mit einer Alpine-Komponente. Auftragsdaten kommen aus JSON-Endpunkten; Schreibaktionen laufen über IndexedDB und anschließend dieselben serverseitigen Services wie weitere GSL-Eingänge.

| Datei / Einstiegspunkt | Funktion und Änderungsbereich |
|---|---|
| [`einheit.html`](../app/templates/einheit/einheit.html) | Konfiguration und Imports ab Z. 4/28; Übersicht ab Z. 176; Auftragsgruppen ab Z. 312; Detail ab Z. 421; Aktionen ab Z. 549; Dialoge/Übermittlungen ab Z. 589/715. |
| [`_einheit_styles.html`](../app/templates/einheit/_einheit_styles.html) | Einheitenkarten, Status/Outbox, Karten, Touchflächen; Mobilregeln ab Z. 110; breites Raster ab Z. 140. Erbt [`_fab_styles.html`](../app/templates/_fab_styles.html), das auch das Fahrtenbuch nutzt. |
| [`einheit_modus.js`](../app/static/js/einheit_modus.js) | Alpine-Zustand; Aktionsableitung ab Z. 72; Initialisierung ab Z. 222; Laden/Detail ab Z. 314/443; Erfassen ab Z. 520; Outbox/Live ab Z. 730/831. |
| [`einheit_karte.js`](../app/static/js/einheit_karte.js) | Sortierhilfe ab Z. 27, Marker ab Z. 53, Leaflet-Rendering ab Z. 109, lokale Standortabfrage ab Z. 187. |
| [`einheit_outbox.js`](../app/static/js/einheit_outbox.js) | IndexedDB ab Z. 42; Versand/Bestätigung ab Z. 198; getrennte Versandspuren ab Z. 279; lokale Erfassung ab Z. 348. |
| [`ui_einheit.py`](../app/routers/ui_einheit.py) | Kontext ab Z. 115/235; Schreibrechte ab Z. 334; Idempotenz ab Z. 371; Zustand/Detail ab Z. 452/681; Status/Meldung/Foto ab Z. 872/934/1013; Medienzugriff ab Z. 1421. |
| [`einheit_service.py`](../app/services/einheit_service.py) | Gesamtansicht/Profil ab Z. 36; Statusmatrizen ab Z. 141; Kontext ab Z. 247; Auftragsscoping ab Z. 370; Kategorisierung ab Z. 428; Statusmutation ab Z. 497. |

## 2. Auftragsdarstellung und Mehrfachzuweisung

Ein Auftrag ist ein bestehender `EinheitSiteDispatch` zwischen `LageEinheit` und `IncidentSite`. Er trägt `auftragsart`, Auftragstext, Einheitenstatus, Reihenfolge, Führungsänderungsversion und Zeitstempel. Erkundung und Abarbeitung können gleichzeitig für dieselbe Einheit an derselben Stelle zugewiesen sein; doppelte **offene** Zuweisungen derselben Art werden im Service abgewiesen. Nach Beendigung ist eine neue Zuweisung möglich. Belege: [`major_incident.py`, Modell ab Z. 704](../app/models/major_incident.py#L704), [`resource_service.py::dispatch_to_site`](../app/services/resource_service.py#L590).

| Bereich | Vorhandenes Verhalten |
|---|---|
| Übersicht | Große Karte für den aktuellen Einsatz, sonst den nächsten Auftrag; bei laufendem Einsatz zusätzlich nächster Auftrag. Adresse, Auftragstext, Navigation, Priorität/Status je nach Karte. Art-Chips sind vor allem in der Auftragsliste sichtbar; die große Übersicht und der Detailkopf zeigen die Art nicht ausdrücklich. |
| Aufträge | Gruppen **Aktuell**, **Nächster**, **Weitere offene**, **Fertig**, **Zurückgezogen**; letzte zwei eingeklappt. Erkundung/Abarbeitung als Chip, Erkundung mit Suchsymbol. „Fertig“ umfasst auch „Nicht durchführbar“. |
| Detail | Auftrag der Einsatzleitung, Adresse/Einsatzgrund, Zielkarte, Fotos, Material, Chronik, Objektverweise, Gefahren im 300-m-Umkreis und Sperren am Ziel. Bei GK-/QR-Zugang sind andere Einheiten ausgeblendet. |
| Erkundung | Hauptaktion „Erkundung abschließen“, Ergebnisstatus `abgeschlossen` mit Label **Erkundet**. Abschlussdialog mit optionaler Lage, notwendigen Maßnahmen und Fotos; ein Bericht ist keine Voraussetzung für den Abschluss. `in_arbeit` ist für Erkundungen nicht erlaubt. |
| Abarbeitung | Hauptaktion „In Arbeit setzen“, danach „Fertig melden“. Zusätzlich Bestätigung, Anfahrt, Vor Ort und begründetes „Nicht durchführbar“, abhängig vom Serverzustand. |
| Karte | Aktueller Auftrag als laufend, nächster mit Pfeilsymbol, übrige mit Nummer; Erkundung zusätzlich gestrichelt. Fertige optional. Verbindung ist Luftlinie; externe Navigation berücksichtigt ECP-Sperren nicht. |
| Änderungen | `NEU` während der Sitzung, `GEÄNDERT` über gespeicherte/gesehene Versionen; Banner bei Umreihung, höherer Priorität oder Rückzug. Öffnen markiert eine Version lokal als gesehen, bestätigt den Auftrag aber nicht beim Server. |

Die JSON-Kategorien enthalten `aktuell`, `weitere`, `abgeschlossen`, `zurueckgezogen`; „Nächster“ ist das erste Element von `weitere`, kein zusätzlicher Datensatz. Alle eigenen Dispatches werden geladen, ohne Pagination. Ein aktueller Auftrag benötigt zugleich einen aktiven Bearbeitungsstatus und den passenden `LageEinheit.incident_site_id`-Zeiger.

Weitere offene Aufträge werden nach **gesetzter Reihenfolge vor ungesetzter**, dann Reihenfolgenwert, Priorität und Zuweisungszeit sortiert. Explizite Leitstellenreihenfolge schlägt Priorität. Die Karte kann mit Standort nur nachfolgende, gleich priorisierte Gruppen ohne explizite Reihenfolge nach Entfernung vorschlagen; laufender und nächster Auftrag bleiben fest. Die Listenreihenfolge ändert sich dadurch nicht. Belege: [`auftraege_fuer_einheit`](../app/services/einheit_service.py#L428), [`berechneReihenfolge`](../app/static/js/einheit_karte.js#L27).

## 3. Zuweisung, Bestätigung und aktive Bearbeitung

„Offene Disposition“ und „aktiv bearbeiteter Auftrag“ bezeichnen unterschiedliche Mengen:

| Zustand | Bedeutung und Nebenwirkungen |
|---|---|
| `zugewiesen` | Führung hat disponiert; `dispatched_at` ist gesetzt. Schon die Disposition kann den Ressourcenstatus von `bereitgestellt` auf `im_einsatz` setzen. Das ist keine Empfangsbestätigung und setzt noch keinen aktuellen Stellenzeiger. |
| `bestaetigt` | Explizite Rückmeldung „Auftrag bestätigt“; erstes `bestaetigt_at`, Statuszeit und letzte Rückmeldung werden gesetzt. Mehrere Aufträge dürfen bestätigt sein. Bestätigung belegt keinen aktiven Bearbeitungsplatz. |
| `anfahrt`, `vor_ort`, `in_arbeit` | `EINHEIT_STATUS_AKTIV`; setzen den aktuellen Stellenzeiger und den Sektor. Ein anderer laufender Auftrag führt zu `409 aktiver_auftrag`, außer bei ausdrücklich bestätigtem Unterbrechen. |
| Unterbrechen | Bisheriger Auftrag wird auf `bestaetigt` zurückgesetzt und protokolliert; kein eigener Pausenstatus. Vorherige Vor-Ort-/Bestätigungszeitstempel bleiben erhalten. |
| `abgeschlossen`, `nicht_durchfuehrbar` | `beendet_at` und ggf. Grund; aktueller Zeiger wird freigegeben. Automatischer Abzug setzt `abgezogen_at`, **nicht** `withdrawn_at`; der Auftrag bleibt in „Fertig“. |
| Zurückgezogen | Führungsentscheidung über `withdrawn_at`; hat bei der Kategorisierung Vorrang. Der gespeicherte Einheitenstatus kann dabei unverändert bleiben. Weitere Statuswechsel werden abgewiesen; verspätete Meldungen/Fotos werden weiterhin angenommen und gekennzeichnet. |

Bestätigung und Anfahrt dürfen übersprungen werden. Direkter Beginn setzt daher nicht automatisch `bestaetigt_at`; `vor_ort_at` wird im Statusservice beim expliziten Status `vor_ort` gesetzt. Die Ressourcenampel im Kopf ist nochmals eine Ableitung und kein Auftragsstatus.

Statusmatrizen in [`einheit_service.py:141`](../app/services/einheit_service.py#L141), Kürzel: Z = zugewiesen, B = bestätigt, A = Anfahrt, V = vor Ort, I = in Arbeit, F = abgeschlossen, N = nicht durchführbar:

| Ausgang | Abarbeitung: erlaubte Ziele | Erkundung: erlaubte Ziele |
|---|---|---|
| Z | B, A, V, I, N | B, A, V, F, N |
| B | A, V, I, N | A, V, F, N |
| A | B, V, I, N | B, V, F, N |
| V | B, I, F, N | B, F, N |
| I | B, V, F, N | keine |
| F / N | keine | keine |

Gleicher Status ist im Service ein No-op; zurückgezogene Aufträge sind davon ausgenommen. Für Abarbeitung sind `in_arbeit` und `abgeschlossen` aus den Stellenphasen `eingegangen`/`erkundung` gesperrt; der Führungspfad `quelle="funk"` ist ausgenommen. Die Detail-API entfernt diese Ziele aus `erlaubte_status`. Wiedereröffnen ist ein separater Führungsvorgang.

## 4. Stellenphase und GSL-Board

Die Stellenphase folgt nach Disposition/Status/Rückzug der bestehenden [`gsl_phase_automatik.py::_zielphase`](../app/services/gsl_phase_automatik.py#L15):

| Dispatch-Situation | Automatische Stellenphase |
|---|---|
| Offene Erkundung, keine vorrangige Abarbeitung | `erkundung` |
| Erkundungen alle beendet oder zurückgezogen, keine vorrangige Abarbeitung | `bewertet` |
| Offene Abarbeitung, noch kein Beginn vor Ort | `disponiert`; eine bereits laufende Phase `in_arbeit` bleibt erhalten |
| Mindestens eine offene Abarbeitung `vor_ort` / `in_arbeit` | `in_arbeit` |
| Nicht zurückgezogene Abarbeitungen alle beendet | `fertig_gemeldet` |
| Stelle bereits `erledigt` / `abgebrochen` | Automatik verändert diese Phase nicht |

Abarbeitungen haben in der Ableitung Vorrang vor Erkundungen. Auch `nicht_durchfuehrbar` zählt als beendet; `fertig_gemeldet` beweist deshalb keinen erfolgreichen Abschluss. Für ausschließlich zurückgezogene Abarbeitungen ohne Erkundung liefert die Automatik kein neues Ziel: die bisherige Phase kann stehen bleiben. **Endgültiges `erledigt` („Abgeschlossen“) und Wiedereröffnung bleiben Führungsaktionen.**

Nach erfolgreichem API-Commit sendet [`ui_einheit.py::_senden`](../app/routers/ui_einheit.py#L443) `site:card_changed`, `einheit:changed` und bei Bedarf `site_phase_changed`; geänderte Phasen aktualisieren zusätzlich GSL-Livezähler. Das [`Board`](../app/static/js/lage_board.js#L164) lädt Karten oder Phasenspalten nach. Seine Karten zeigen nach Reihenfolge sortierte Einheitenstatus-/Art-Chips und Rückmeldungsindikatoren aus den bestehenden Projektionen; automatisch abgezogene fertige Einheiten verschwinden aus den Chips. SMS und Tablet-Push für neue/geänderte/zurückgezogene Aufträge werden in [`gsl_auftrag_events.py`](../app/services/gsl_auftrag_events.py) nach dem Commit ausgeführt; Zustellung ist keine Bestätigung.

Der Begriff „aktiv“ ist hier besonders riskant: [`dispatch_aktiv_filter`](../app/services/resource_service.py#L130) meint **nicht zurückgezogen und nicht beendet**, also auch nur zugewiesene Aufträge. Änderungen an diesem Filter beeinflussen Disposition, Board-Zähler, Lagemeldungstimer, Ressourcen und Live-/Widget-Daten.

## 5. Lagemeldungen, Fotos, Verlauf und Offline-Outbox

- **Meldungen:** Lagemeldung, Maßnahme und Notiz als `SiteLogEntry`, mit Einheiten-/Autorbezug und optionaler Geräte-Erfassungszeit; maximal 4.000 Zeichen. Die API akzeptiert Freitext oder formatiert strukturierte Felder. Entwürfe werden pro Dispatch/Art lokal gespeichert. Nur `lagemeldung` setzt den Lagemeldungs-Regelkreis zurück und schließt offene automatische Funkjournal-Anforderungen. Der Timer beginnt ab Bestätigung oder aktivem Status, bei konfiguriertem Intervall; bloße Zuweisung genügt nicht. Belege: [`meldung`](../app/routers/ui_einheit.py#L934), [`site_log_service.py`](../app/services/site_log_service.py), [`lagemeldung_service.py`](../app/services/lagemeldung_service.py#L57).
- **Fotos:** Kamera-/Dateiauswahl, clientseitige Kompression, serverseitige Bild-/Größenprüfung und Speicherquota über [`lage_media_service.py`](../app/services/lage_media_service.py). Kommentar bis 500 Zeichen, Autor/Einheit/Erfassungszeit. Die normale Foto-Schnellaktion erfasst ohne Kommentar; ein Kommentar-Flyout existiert im Template, wird von dieser Aktion aber nicht geöffnet. Galerie mit Thumbnails und Original-Link; kein Videoablauf.
- **Meldungsverlauf:** Detail zeigt stellenbezogene Chronik, neueste zuerst, maximal 50 Einträge. Tablet/angemeldeter GK-Benutzer sehen auch andere Einheiten an der eigenen Stelle; GK-/QR-Link werden auf eigene Einträge und Fotos gefiltert. Lokale „Zuletzt übermittelt“-Liste ist ein Versandnachweis mit Zeit/Art/Serverantwort, kein vollständiges Meldungsarchiv: 20 angezeigt, maximal 200 gespeichert, Bereinigung nach 72 Stunden. Nicht übermittelte Outbox-Einträge werden dabei erhalten.
- **Lokaler Speicher:** IndexedDB-Stores `outbox`, `verlauf`, `entwuerfe`, `zustand`, `meta`; monotone Sequenz und `client_uuid`. Fotos liegen als ArrayBuffer vor, um bekannte Safari-Probleme mit aus IndexedDB gelesenen Blobs zu umgehen. UUID-Fallback, Öffnungs-Timeout und erneuter Speicherzugriff nach Fehler sind vorhanden.
- **Versand:** Reihenfolge je Dispatch für Status/Meldungen/Material; verschiedene Dispatches können parallel senden. Fotos laufen unabhängig, Materialfotos warten auf die zugehörige Materialanlage. Netz-/Serverfehler werden mit 2–60 Sekunden Rückoff erneut versucht. `409`/`403`/`404` werden Konflikte; weitere Einträge können weiterlaufen. Bestätigung erst bei erfolgreicher HTTP-Antwort **mit `aktion_id`**, ohne Offline-Header. [`EinheitAktion`](../app/models/major_incident.py#L750) speichert UUID und Antwort für idempotente Wiederholung.
- **Offline-Lesen:** [`sw.js:121`](../app/static/sw.js#L121) speichert erfolgreich geladene HTML-/Zustands-/Detailantworten; Fallback trägt `X-EC-Offline: 1`. Zusätzlich hält IndexedDB den letzten Gesamtzustand. Ohne zuvor geladenes Detail stehen nur Basisdaten zur Verfügung. Keine garantierte vollständige Offline-Karte: Kacheln/Navigation und echte Gerätelaufzeiten sind getrennt zu prüfen.
- **Synchronisation:** WebSocket, Online-/Sichtbarkeitswechsel, Android-Resync und 30-Sekunden-Polling; Zustand nutzt ETag/304. Die UI unterscheidet serverbestätigten und lokal ausstehenden Status. Bei terminalem GK-/QR-Zugangsfehler stoppt der Versand, offene Einträge bleiben als `blockiert_zugang` kopierbar. Das ist keine Empfangsbestätigung durch die Einsatzleitung.

Digitale Anforderungs-/Chat-/Quittierungsketten sind kein bedienbarer Bestandteil dieser Ansicht. Namen in `ZUGANG_AKTIONEN` allein belegen keine Endpunkte. Der [konsolidierte Gesamtplan, G4](plans/gsl-ressourcen-einheitenmodus-gesamtplan.md) streicht diese Funktionen ausdrücklich; der ältere [Einheitenmodus-Plan](plans/gsl-einheitenmodus-plan.md) enthält überholte Bestandsaussagen, etwa eine damals noch fehlende Outbox.

## 6. Zugangs- und Berechtigungsgrenzen

| Zugang | Auflösung und Schreibumfang | Lesesicht / Einschränkungen |
|---|---|---|
| Persönlicher GK-Link | Cookie `ec_gk`, Zugangstyp `personal`; aktuelle Führung, Telefonversion, Generation, Aktivierung, Ablauf und ggf. PIN-Verifikation werden live geprüft. Status/Meldungen/Fotos/Material nur über eigenen Dispatch. | Keine Gesamtansicht; andere Einheiten ausgeblendet; Chronik/Fotos/Material auf eigene Einheit begrenzt. Personal/Ausstattung nur mit `gk_zugang_ressource_pflegen`. |
| QR-Einheitenzugang | Eigenes Cookie `ec_qr`, Zugangstyp `qr`, optional eigene PIN; ohne Telefonnummer und auch ohne zugeordneten GK nutzbar. Eigene Status/Meldungen/Fotos/Material. | Gleiche reduzierte Dispatch-Sicht. Personal/Ausstattung **immer serverseitig gesperrt**, auch wenn das Org-Flag aktiv ist. |
| Geräteprofil `einheit` | Session-Token → aktives `DeviceToken` → Fahrzeug → eigene Einheit in aktiver Lage und eigener Organisation. Ohne explizite Lage wird die neueste aktive Lage gewählt. Eigenen Dispatch bei jedem Zugriff erneut prüfen. | Stellenbezogener Kontext einschließlich anderer Einheiten/Chronik/Fotos. Gesamtansicht ist nur über GET/HEAD-Allowlist lesbar; neue Führungsendpunkte sind standardmäßig gesperrt. Andere Geräteprofile behalten ihre bisherigen Rollenrechte. |
| Angemeldeter GK-Benutzer | Zusätzlich vorhanden: Mitgliedsverknüpfung → aktuelle Führungszuordnung. Auswahl mehrerer geführter Einheiten über `X-EC-Einheit`/URL. | Keine allgemeine Freigabe durch Benutzerrolle; Ressourcenpflege an Org-Flag gebunden. Die Sicht ist teilweise weiter als bei GK-Link, etwa bei Stellenchronik/Fotos. |
| Simulation | Admin bzw. berechtigte Planspielleitung; `X-EC-Einheit-Sim` / `?sim=`. | Echtlage nur lesend, Übung schreibbar; gesonderte lokale Outbox und Auditkennzeichnung. |

Belege: [`einheit_kontext` / `einheit_darf`](../app/routers/ui_einheit.py#L235), [`Kontextfunktionen und Gesamtansicht`](../app/services/einheit_service.py), [`sitzung_pruefen_mit_grund`](../app/services/gk_zugang_service.py#L524). „QR“ meint hier den **Einheitenzugang**, nicht den älteren Einsatz-/Lage-QR-Login.

HTTP-Schreibaktionen bleiben CSRF-geschützt. GK-/QR-WebSocket `/ws/einheit-zugang` erhält zentral nur `einheit:changed` für die eigene Einheit; reine Stellenereignisse werden nicht dorthin gespiegelt. GK-/QR-Widerruf wird spätestens beim nächsten Client-Ping geprüft (nominal 25 Sekunden). Geräte-/Benutzersessions nutzen den Lagekanal; Gerätewiderruf wird dort **beim Handshake**, nicht in der laufenden Ping-Schleife geprüft (F13). Belege: [`broadcast.py:126`](../app/services/broadcast.py#L126), [`ws.py`](../app/routers/ws.py#L104). Fremde Dispatch-IDs werden mit 404 abgewehrt. Die direkte Medienprüfung hat allerdings die unter F6 beschriebene Abweichung.

## 7. Schwächen und nachgewiesene UI-Probleme

„Bestätigt“ bedeutet unten lokal reproduziert; „Codebefund“ bedeutet aus dem Ausführungspfad abgeleitet, ohne vollständigen Gerätetest. Priorität **hoch** betrifft Datenverlust, Zugriffsgrenzen oder veraltete Arbeitsinformationen; **mittel** betrifft Bedienung und Transparenz.

| ID / Priorität | Befund und Auswirkung | Konkrete Änderungsstelle |
|---|---|---|
| F1 · hoch · bestätigt | **Doppelte Initialisierung:** Alpine ruft eine `init()`-Methode bereits automatisch auf; zusätzlich steht `x-init="init()"` im Template. Lokaler Chromium-Nachweis mit mitgeliefertem Alpine: zwei Aufrufe. Die Methode hat keinen Guard und registriert Listener, Outbox und Timer erneut; WebSocket-Checks verhindern eine zweite Verbindung im Zustand CONNECTING nicht. | `einheit.html:40–41`; `einheit_modus.js::init`, `verbinden`. |
| F2 · hoch · bestätigt | **Geöffnete Details veralten bei Polling:** `laden()` aktualisiert Kategorien, nicht `detail`. Nach externem Statuswechsel zeigte die Liste `bestaetigt`, das offene Detail weiter `zugewiesen`. Ohne passendes WS-Ereignis bleiben Aktionen/Chronik veraltet; bei GK/QR kommen reine Stellenereignisse nicht an. Der ETag des Gesamtzustands bildet Chronik/Fotoinhalte nicht ab. | `einheit_modus.js::laden`, `zustand`, WS-Handler; API `zustand`/`auftrag_detail`; `broadcast.py::broadcast_lage`. |
| F3 · hoch · bestätigt | **Eingaben gehen bei lokalem Speicherfehler verloren:** `outboxErfassen()` kann `null` liefern, danach leert `meldungErfassen()` trotzdem Text/Felder, löscht den Entwurf und schließt. Ähnliche fehlende Erfolgsprüfungen bei Status-/Foto-/Erkundungserfassung. Bei Erkundung sind Bericht, Fotos und Abschluss getrennte Einträge; ein Abschluss kann trotz fehlgeschlagenem Foto gespeichert/übermittelt werden. | `einheit_modus.js:144`, `statusInOutbox`, `erkundungAbschliessen`, `meldungErfassen`, Fotoaktionen. |
| F4 · mittel · bestätigt | **Nur strukturierte Lagemeldung wird nicht erfasst:** Client kehrt ohne Freitext sofort zurück; bei vorhandenem Freitext verwendet der Server diesen statt der formatierten Felder. Strukturierte Zusatzangaben gehen so nicht in den Chroniktext ein. | `einheit_modus.js::meldungErfassen`; `ui_einheit.py::meldung`; `site_log_service.py::format_lagemeldung`. |
| F5 · mittel · bestätigt | **Fehlende Koordinaten werden zu 0/0:** `Number(null)` ist 0; `hatKoordinaten` meldet deshalb gültige Werte. Im gerenderten Detail ohne Koordinaten erschien die Karte am Nullpunkt, OSM-Link enthielt `null`. | `einheit_modus.js:99`, `osmUrl`, `karteAktualisieren`; `einheit.html:436`. |
| F6 · hoch · bestätigt | **Medienrecht inkonsistent:** Bei GK/QR filtert `auftrag_detail` fremde Einheitenfotos aus der Liste. `_medium()` prüft nur einen eigenen Dispatch zur selben Stelle. Ein synthetisches Foto einer anderen Einheit war in der Liste unsichtbar, aber per Original- und Thumbnail-URL mit HTTP 200 abrufbar. Die Prüfung berücksichtigt auch keinen Rückzug. Das belegt Zugriff innerhalb einer zugewiesenen Stelle, keinen organisationsübergreifenden Zugriff. | `ui_einheit.py:788`, `_medium` ab Z. 1421. Beabsichtigte Freigabe nach Rückzug ausdrücklich festlegen. |
| F7 · mittel · bestätigt | **QR-Ressourcenpflege wird fälschlich angeboten:** `kann_ressource_pflegen` berücksichtigt das Org-Flag, aber nicht `zugang.typ == "qr"`. API-Zustand lieferte `true`, während die Schreibendpunkte korrekt 403 liefern. | `ui_einheit.py::zustand` Z. 461; mit `einheit_darf` Z. 346 abgleichen. |
| F8 · mittel · bestätigt | **Eigene Chronik kann leer werden:** Erst werden die letzten 50 Stellenlogs geladen, dann eigene Einträge gefiltert. Ein eigener älterer Eintrag und 51 neuere fremde Einträge ergaben für QR eine leere Chronik. | `ui_einheit.py::auftrag_detail` Z. 769–786: Einheitenfilter vor `limit(50)`. |
| F9 · mittel · Codebefund | **Offline-Basisdetail ohne Statusaktionen:** `_auftragsdaten` enthält kein `erlaubte_status`; beim Offline-Öffnen ohne gecachtes Detail ist die Liste leer. Direkte Komponentenprobe bestätigt keine Hauptaktion. Ein nach lokalem `in_arbeit` möglicher Abschluss bleibt außerdem bis zur Übermittlung deaktiviert. Bei Rückzug liefert die Detail-API umgekehrt noch Statusziele, die der Service anschließend ablehnt; `withdrawn_at` fehlt in der Projektion. | `einheit_service.py::_auftragsdaten`; `ui_einheit.py::auftrag_detail`; `einheit_modus.js::oeffne`/Statusgetter; Template Z. 557. |
| F10 · mittel · Codebefund | **Änderungshinweise sind unvollständig:** `NEU` wird nur bei zuvor nicht leerer weiterer Queue erkannt und nur unter „Weitere offene“ als Chip gezeigt. Erster/neuer nächster Auftrag kann deshalb ohne `NEU` bleiben. `auftragGeaendert` vergleicht die zuvor geöffnete Version ohne Dispatch-ID; Wechsel zwischen unterschiedlichen Aufträgen kann einen falschen Änderungsalarm erzeugen. | `einheit_modus.js::zustand` Z. 372/390, `oeffne` Z. 452; Listenmarkup Z. 314–373. |
| F11 · mittel · Codebefund | **„Erneut senden“ löst gespeicherte 409 nicht neu aus:** Die Outbox verwendet dieselbe UUID; der Server spielt den protokollierten Konflikt wieder ab, selbst wenn dessen Ursache inzwischen behoben ist. Für `aktiver_auftrag` existiert bereits ein gesonderter Wechselablauf mit neuer Aktion. | `einheit_outbox.js::erneutSenden`; `ui_einheit.py::fuehre_aktion_aus`; `einheit_modus.js::konfliktEntscheiden`. |
| F12 · hoch · Codebefund | **Lokale Daten sind unvollständig an den Principal gebunden:** Geräte verwenden global `ec-einheit`, GK/QR nur Typ und Einheiten-ID, keine Zugangsgeneration; Benutzer nur Einheiten-ID. Rotation/Benutzer-/Fahrzeugwechsel benötigen eine bewusste Regel für alte Einträge. SW-Cache-Schlüssel unterscheiden Benutzer-/Simulationsheader nicht. Das Leeren entfernt `/einheit` und APIs, aber keine HTML-Deep-Links `/einheit/auftrag/<id>`. Nach terminalem Fehler bleibt `schreibbar` aus der Startkonfiguration erhalten. | `einheit_modus.js::outboxName`, `zugangBeenden`, Getter `schreibbar`; `sw.js:80–87`, `123–133`. |
| F13 · hoch · bestätigt | **Bestehender Geräte-WebSocket bleibt nach Widerruf offen:** `_resolve_user()` prüft den Token beim Handshake; `lage_ws()` beantwortet spätere Pings ohne erneute Prüfung. Lokal: Verbindung aufgebaut, Token widerrufen, weiterhin `pong`; neuer HTTP-Zugriff auf den Einheitenzustand dagegen 403. Der bestehende Socket bleibt im Lagekanal registriert. GK/QR haben bereits eine regelmäßige Prüfung. | `ws.py::_resolve_user` Z. 104, `lage_ws` Z. 226–256; Geräteprüfung auch im laufenden Kanal und Clientbehandlung terminaler Close-Codes prüfen. |

### Smartphone und Tablet

Bereits umgesetzt sind Tabs bis 760 px mit Safe-Area-Abstand, große Hauptaktionen (56 px), einspaltige Aktionsreihen und Anpassungen für lange Kopftexte. Die früher überdeckende mobile Aktionsleiste ist im Code ausdrücklich auf `position:static` geändert. UUID-/IndexedDB-Fehlerfälle und Safari-Fotoblob-Probleme haben bereits Gegenmaßnahmen; sie sind keine pauschal noch offenen Fehler.

Aktuell folgen im Smartphone-Detail die Status-/Meldeaktionen **nach** Karte, Informationen, Galerie, Material und Chronik. In einer lokalen Ansicht mit 390 × 844 px und zwölf kurzen Chronikzeilen begann der Aktionsbereich erst bei etwa 1.795 px. Die Aktion ist erreichbar, aber ohne längeres Scrollen nicht sichtbar. Die Tab-Leiste ist im Detail ausgeblendet; der Rückweg ist oben „Meine Einsätze“ oder Browser-Zurück. Art, Bestätigung und Bearbeitung sollten auch in Übersicht/Detail klar benannt sein.

Ab 1.024 px oder ab 900 px im Querformat erscheinen Karte und Auftragsliste nebeneinander. Zwischen 900 und 979 px gilt gleichzeitig noch `max-width:720px` aus dem gemeinsamen Layout; die rechte Spalte verlangt mindestens 22 rem. Das ist ein konkreter Prüfpunkt für schmale Tablet-Querformate, kein nachgewiesener flächendeckender Überlauf. Listenzeilen mit vielen Chips, lange Adressen, Bildschirmtastatur und die mobilen Dialoge (`88vh` gegenüber geerbtem `100dvh`) benötigen echte Layout-/Bedientests. Eine Tastatur-/Fokusführung für die Dialoge ist in der Einheitenkomponente nicht umgesetzt.

## 8. Abhängigkeiten, Risiken und kleine kompatible Änderungsschritte

| Abhängigkeit / Risiko | Folgerung für spätere Änderungen |
|---|---|
| Gemeinsame Services für Führung, Einheitenansicht, Funk, MCP und Planspiel | Statusmatrizen, `dispatch_aktiv_filter`, Abschlussautomatik und Journalquellen erhalten. Ein bloßes UI-Redesign darf die Stellenphase nicht ändern. |
| Datenkonsistenz bei mehreren Geräten | Exklusivität aktiver Bearbeitung ist ein Service-Check; das Modell zeigt dafür keine DB-Unique-Absicherung, der Pfad keine entsprechende Zeilensperre. Gleichzeitige Starts verschiedener Dispatches sind ein noch ungeprüftes Rennen, insbesondere unter MariaDB. Keine bewiesene atomare Garantie behaupten. |
| Idempotenz / Versionssemantik | UUID bei Transport-Retry erhalten. `version` zählt Führungsänderungen; Einheitenstatus erhöht sie nicht. Die Status-API erzwingt keine Übereinstimmung mit `auftrag_version`; Meldungen geben bei Abweichung einen Hinweis. Das ist keine allgemeine optimistische Sperre. |
| WebSocket, Redis-Bus, ETag, Polling | Korrekte Bedienung muss auch ohne WS und bei 304 funktionieren. Neue Einheitsereignisse dürfen keine fremden Daten an GK/QR senden. Polling darf keine geöffneten Eingaben überschreiben. |
| Medien, Quota, Material/SMS/Druck | Uploads und Abschluss sind nicht atomar gekoppelt. Material bleibt featureabhängig und nutzt bestehende Nebenwirkungen nach Commit; keine zweite Versand- oder Druckpipeline einführen. |
| Gemeinsames Fahrtenbuch-CSS | Erste Layoutkorrekturen unter `.einheit-page` begrenzen; Änderungen an `_fab_styles.html` benötigen zusätzlich Fahrtenbuch-Smoketests. |
| SW-/IndexedDB- und Asset-Versionen | Existierende lokale Einträge und alte Browserassets müssen lesbar bleiben. Importmap/`ASSET_VERSION` berücksichtigen; neue Cache-/Principal-Trennung darf ungesendete Daten nicht still löschen. |
| Android-/Widget-Vertrag | [`gsl_live_service.py:58`](../app/services/gsl_live_service.py#L58) adaptiert dieselbe Queue. Ohne laufenden Auftrag wird dort der nächste als `current` geliefert; bestehende Feldnamen und Bedeutung für Altclients erhalten. |

Empfohlene Reihenfolge, **hier nicht implementiert**:

1. **Bestehende Pfade reparieren:** genau eine Initialisierung; Speichererfolg vor Leeren/Schließen prüfen; Koordinaten explizit auf fehlende Werte prüfen; QR-Fähigkeitsflag an die vorhandene Autorisierung angleichen; Chronikfilter in SQL vor Limit setzen; Medienrechte für GK/QR und Gerätewiderruf am laufenden WebSocket konsistent prüfen. Keine neuen Statuswerte oder Tabellen.
2. **Listen und Details zuverlässig aktualisieren:** geöffnetes Detail bei Polling/Resync gezielt nachladen, unabhängig vom Listen-304; fremde/ältere Antworten bei schnellem Auftragswechsel abweisen. Lokale Formulare und Entwürfe dabei bewahren. Eindeutige Art- und Status-Chips auch auf Übersicht und Detail, vollständige `NEU`-Anzeige und IDs beim Versionsvergleich.
3. **Kleine additive API-Projektion:** `erlaubte_status` und Rückzugskennzeichen auch im Zustands-Payload anbieten, bestehende Kategorien/Felder behalten. Rechte/Phasenregeln im Backend zentral ableiten. Alte gecachte Zustände ohne Zusatzfelder weiter lesbar halten und Offline-Grenzen sichtbar machen. Fachliche Konflikte bewusst als neue Aktion erfassen; Transport-Retry bleibt identisch.
4. **Mobil gezielt verbessern:** kompakte Status-/Foto-/Meldeaktionen am Detailanfang, vollständige Informationen darunter; bestehende Bedienelemente wiederverwenden. Tabletbreite und Langtextumbruch einheitenspezifisch korrigieren, ohne verdeckende Leisten einzuführen.
5. **Lokale Kontextwechsel getrennt absichern:** stabile Principal-/Generationsbindung und Cachetrennung entwickeln, alte Queues mit sichtbarer Wiederherstellungs-/Verwerfungsentscheidung erhalten. Dies ist ein eigener Schritt mit höherem Kompatibilitätsrisiko als reine Darstellungskorrekturen; keine automatische Neuübermittlung alter Aktionen unter neuen Rechten.

## 9. Geeignete Regressionstests und durchgeführte Prüfung

| Testebene / vorhandene Anker | Konkrete Ergänzung bzw. zu erhaltendes Verhalten |
|---|---|
| [`test_einheit_service.py`](../tests/test_einheit_service.py), [`test_gsl_phase_automatik.py`](../tests/test_gsl_phase_automatik.py) | Mehrere Zuweisungen/Bestätigungen ohne aktiven Auftrag; explizite Reihenfolge vor Priorität; genau ein laufender Auftrag; Unterbrechen; beide Statusmatrizen; direktes Beginnen; Erkundungs-/Abarbeitungsabschluss bis `fertig_gemeldet`, endgültiger Abschluss nur Führung. Gleichzeitige Starts zusätzlich unter MariaDB prüfen. |
| [`test_einheit_api.py`](../tests/test_einheit_api.py), [`test_gsl_dispatch_aktiv_filter.py`](../tests/test_gsl_dispatch_aktiv_filter.py) | Autor/Quelle, UUID-Replay, Rückzug vs. Abzug, verspätete Meldungen/Fotos, Zeitkorrektur, Führungsänderungsversion; neue additive Status-/Rückzugsprojektion und Alt-Payloads (F9/F11). |
| [`test_einheit_zugang.py`](../tests/test_einheit_zugang.py), [`test_gk_qr_zugang.py`](../tests/test_gk_qr_zugang.py), [`test_einheit_ressource_pflege.py`](../tests/test_einheit_ressource_pflege.py) | Eigene/fremde Dispatches, Org-Grenze, Rotation/Widerruf, QR ohne Leader; QR-Flag false bei aktivem Pflegeflag; fremde Stellenfotos direkt und als Thumb abweisen; eigene Logs trotz >50 neuer fremder Logs sichtbar (F6–F8). |
| [`test_einheit_gesamtansicht.py`](../tests/test_einheit_gesamtansicht.py), [`test_einheit_benutzer.py`](../tests/test_einheit_benutzer.py), [`test_ws_einheit_zugang.py`](../tests/test_ws_einheit_zugang.py), [`test_ws_geraete_widerruf.py`](../tests/test_ws_geraete_widerruf.py) | Gesamtansicht lesen, Führungsmutationen sperren; andere Geräteprofile unverändert; verknüpfter Benutzer/Einheitswechsel; gefilterte WS-Ereignisse. Bestehende Gerätetests prüfen Widerruf vor Handshake; um Widerruf nach Verbindung samt Close-Code und Ende der Zustellung ergänzen (F13). |
| [`einheit_outbox.test.mjs`](../tests/js/einheit_outbox.test.mjs) | Neustart, UUID-Erhalt, strikte Reihenfolge pro Dispatch, unabhängiger Fotoversand, Materialabhängigkeit, Backoff, terminaler Zugang und fehlender Fotoinhalt erhalten. Zusätzlich Speicherfehler bei Erkundungs-Bündel, Konflikt-Replay, mehrere Tabs sowie Principal-/Generationswechsel testen (F3/F11/F12). |
| Neue Verhaltenstests für `einheit_modus.js` | F1–F5/F9/F10: einmalige Initialisierung; strukturierte Meldung; Fehler erhält Text/Entwurf/Datei; null/leere Koordinaten ohne Marker; Offline-Basisdetail; erster neuer Auftrag; Wechsel zwischen unterschiedlichen Versionen verschiedener Dispatches. Nur Stringsuche im Quelltext reicht dafür nicht. |
| [`einheit_karte.test.mjs`](../tests/js/einheit_karte.test.mjs), [`einheit_aenderungen.test.mjs`](../tests/js/einheit_aenderungen.test.mjs), [`test_einheit_karte_api.py`](../tests/test_einheit_karte_api.py) | Serverrang, fixierter nächster/laufender Auftrag, Erkundungsmarker, Navigation ohne Koordinaten und HTML-Escaping erhalten. |
| [`test_gsl_board_einheiten.py`](../tests/test_gsl_board_einheiten.py), [`test_gsl_live_einheit_adapter.py`](../tests/test_gsl_live_einheit_adapter.py) | Einheitsstatus/Art auf Boardkarten, Rückmeldungs-/Fertigindikatoren und Queue für Geräte-/Widget-Altclients erhalten; Polling ohne WS muss offene Details aktualisieren (F2). |
| Neue Browser-/Geräteabnahme | 360/390 px Smartphone, 768 px Tablet-Hochformat, 900/979/980/1.024 px Querformat-Grenzen; viele Aufträge/Logs/Fotos und lange Texte. Status, Lagemeldung, Kamera, Tastatur, Zurück, Dialogfokus und Rotation bedienen. Online → Funkloch → Reload → Sync; WS-Ausfall/304; Wechsel GK ↔ QR ↔ Gerät/Simulation einschließlich gecachter Deep-Links (F1–F3/F5/F9/F12). Safari und echte WebViews gesondert. |

Ausgeführt auf unverändertem Anwendungscode:

```bash
.venv/bin/pytest -q tests/test_einheit_*.py \
  tests/test_gk_qr_zugang.py tests/test_ws_einheit_zugang.py \
  tests/test_ws_geraete_widerruf.py tests/test_gsl_phase_automatik.py \
  tests/test_gsl_board_einheiten.py tests/test_gsl_dispatch_aktiv_filter.py \
  tests/test_gsl_live_einheit_adapter.py tests/test_lagemeldung_service.py

node --test tests/js/einheit_outbox.test.mjs \
  tests/js/einheit_karte.test.mjs tests/js/einheit_aenderungen.test.mjs
```

Ergebnis: **162 Python-Tests bestanden** (11,68 s), **42 JavaScript-Tests bestanden**. Python meldete 43 Warnungen (SlowAPI-Deprecation und SQLite-Tabellenabbau bei zyklischen Fremdschlüsseln); Node meldete die bestehende fehlende Modul-Typdeklaration. Kein Testfehler.

Zusätzliche einmalige Nachprüfungen, ohne neue Testdateien im Repository: Node-Komponentenproben für fehlende Koordinaten, strukturierte Meldung, Speicherfehler und Offline-Basisdetail; Chromium mit echten Templates/Assets und synthetischen Antworten über FastAPI-TestClient, ohne echten WS; API-Prüfung von QR-Fähigkeitsflag, Chroniklimit und direkten Medienrechten; gesonderte TestClient-WebSocket-Prüfung von Gerätewiderruf nach dem Handshake. Die regulären Tests decken diese Lücken bislang nicht vollständig ab. Die unter `test_einheit_mobil.py` vorhandenen Mobilprüfungen testen Statusdaten und Template-Merkmale, keine vollständige Browserbedienung.
