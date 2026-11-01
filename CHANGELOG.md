# Versionshistorie

Die Versionshistorie wird reverse-chronologisch geführt. Jede Version nennt oben die Zusammenfassung der Release-Notes und darunter unter „Details“ die ausführlichen Einträge. Versionen vor 2026.10.06 stehen in der Tabelle am Ende.

## Unreleased

- Systemdiagnose: lesende App-Geraeteflotte mit Filtern, Release-Ampel, Detailansicht und gemeinsamem Gesundheitscheck `app.geraete`.

- **App-Geraetebericht:** Neuer Endpunkt `POST /api/v1/device/diagnose-report` nimmt den Geraetebericht der Android-App entgegen (Version, Geraete-/Display-/Android-Eigenschaften, WebView, Einstellungen, Berechtigungen, Dienstzustand, strukturierte Fehler). Whitelist-Validierung, Maskierung, 64 KB und 30 Berichte/Stunde, 90 Tage Aufbewahrung; kein Rueckkanal fuer Einstellungen. Migration 0339.

- **Admin-MCP / Deploy:** Die Systemuebersicht vergleicht den Deploy-Commit mit main und warnt bei Rueckstand. Ein bestaetigt aktueller Stand schliesst alte Versionsbefunde; GitHub-Tokenbefunde bleiben offen. Ein nicht verfuegbarer GitHub-Abgleich verschlechtert den Status nicht.

- **Admin-MCP / Push:** Neues lesendes Tool `admin_push_pruefen` fuer FCM-Konfiguration, Token-Faehigkeiten je Organisation und letzte Zustellversuche samt Empfangsbestaetigungen und ACK-Zeiten, ohne Tokenwerte, Namen oder Nachrichteninhalte. Keine Migration.

- **Android-Push-Test:** Neuer Endpunkt `POST /api/v1/device/fcm-test-push` testet normale Push-Benachrichtigungen ohne Einsatzalarm, mit eigener 20-Sekunden-Sperre. Keine Migration.

- **Admin-MCP:** Anmeldungen inklusive 2FA-Bestaetigung gelten absolut 365 Tage statt acht Stunden; Access-Tokens bleiben 15 Minuten gueltig und werden per Refresh erneuert. Erneute Anmeldung mit bestehender Client-Registrierung nach Ablauf oder Widerruf durch OAuth-Regressionstests abgesichert. Keine Migration.

- **SMS-Kurzlink:** `{link}` und `{anmeldelink}` in der SMS-Einsatzinfo sind für neue Einsätze Kurzlinks `/a/<10 Zeichen>` (Base58, nur als Hash gespeichert, gleiche Gültigkeit und Limits wie `/alarm/…`). Optionale Kurz-Domain über `SHORT_BASE_URL`; bestehende Einsätze und Langlinks funktionieren weiter. Migration 0338.

- **SMS-Vorschau:** zeigt nur echte Einsätze (keine Übungen) und je Einsatz eine Tabelle, welcher Platzhalter welchen Inhalt hatte. Profilmenü: „Über die App“ heißt „App-Einstellungen“; Übersichts-Button wieder „+ Neuer Einsatz“.

- **Nativer Einsatzalarm (Android):** App-Versionen mit der Fähigkeit `native_alarm` erhalten den Einsatzalarm als eine hochpriorisierte FCM-Datennachricht (Einsatz-ID, Übungskennzeichen) und spielen den Alarmton selbst über den Wecker-Kanal mit Vollbildanzeige ab. Ältere App-Versionen erhalten weiter Weck- und Anzeige-Nachricht. Neuer Test-Endpunkt `POST /api/v1/device/fcm-test-alarm` (höchstens alle 20 s). Migration 0337.

- **SMS-Einsatzinfo:** Vorschau von Erstalarm und Nachalarmierung für die letzten fünf Einsätze, mit tatsächlichen Versandtexten oder aktueller Vorlage sowie Zeichen- und SMS-Segmentanzahl.

- **Brandschutzplan-Overlay:** Die Einstellungen stehen auf der Einsatzinfo-Seite unter der Karte statt darüber und sind im Dunkelmodus lesbar gestaltet (Auswahlfeld, Schieber, Aktionsbuttons, Hinweise).

- **Teams-Alarmkarte:** Gemeinsame Alarmdaten mit der Einsatz-Mail einschließlich Objekt/Gefahren, farbiges Stichwort-Badge, Übungsbanner und übersichtliche Detailfelder. Adaptive Card 1.4 in voller Breite; Navigation auch über die Adresse, Inhalts-Schalter und PIN-Anmeldung bleiben erhalten.

- **Offline-Objekte:** Listen mit Suchparametern und URL-Varianten verwenden den synchronisierten Bestand. Ältere Objektcaches können auf die Einsatzansicht zurückfallen. Fehlende Seiten zeigen eine lokale Offline-Hilfe mit Direktlinks statt der Startseite oder einer technischen Fehlermeldung.

- **Einsatzlink-Gast:** Neue PIN-/Namen-Anmeldungen verwenden einen separaten Nur-Lesen-Principal (readonly); Schreibzugriffe einschließlich Notizen, Uploads und Funkjournal sind gesperrt. Direkte PIN-Anmeldung in Teams-Karte und Einsatz-Mail, optionaler SMS-Platzhalter `{anmeldelink}`. Bestehende Gast-Sessions und QR-Druck-Zugang bleiben unverändert.

- BSP-Overlay: Freie Polygon-Ausschnitte im Georef-Editor mit Touch-Griffen, Geometrieprüfung
  und MCP-Entwürfen; transparente Kartenraster und weiß maskierte, unverzerrte Druckausschnitte.

- **BSP-Verwaltung:** Geführter Ablauf mit drei Schritten, Overlay-Karten mit Statuschips, verständlicher Lagegenauigkeit und Freigabezusammenfassung. Ausschnitt-Markierung hebt den Plan hervor; Anzeige und eingeklappte Druck-Einstellungen sind getrennt. Ungespeicherte Änderungen, fehlende Georeferenz und Fehler sind sichtbar; Bedienung auch am Handy.

- **Objektkarte:** BSP-Overlays im Karten-Tab und Vollbild-Editor lokal ein-/ausblenden und Deckkraft einstellen; einklappbare Bedienung unter der Karte auch mobil. Verwalter erhalten einen Hinweis auf nicht freigegebene/veraltete Overlays, ohne Entwurfsraster auszuliefern. Marker und Geoman bleiben bedienbar.

- **Objekt-Einsatzansicht:** Freigegebene, aktuelle BSP-Overlays erscheinen auch in der Lagekarte der Objekt-Einsatzansicht unter den Objektsymbolen, mit lokalem Schalter und Deckkraft; geschuetzte Objekt-JSON-Route ohne Offlinecache.

- **Objektkarte / BSP-Overlay:** Freigegebene Planausschnitte erscheinen lagegenau als Raster-Ebene in der Einsatzkarte. Verwaltung im Georef-Editor (Ausschnitt, Vorschau, Freigabe mit einzeln bestätigten Warnungen); Ebene in Lageführung mit Opt-out (für alle bzw. nur für mich) und Deckkraft, lesend in Einsatzinfo-, GSL- und Einheitenkarte. Veraltete oder zurückgezogene Overlays werden nie ausgeliefert. Druck getrennt vom Bildschirm: vier Modi im Lageführung-Druckdialog, regelbasierter Ausdruck des BSP-Ausschnitts über die Druckregel (nur mit `autodruck`, idempotent je Freigabe). MCP: `objekt_bsp_overlay_entwurf` (nur Entwurf) und Lesefelder `bsp_overlays`. Migration 0336. Doku: [BSP-Overlay](docs/architecture/bsp-overlay.md).

- **Objektkarte / BSP-Arbeitsliste:** Nur Lageplan-Seiten werden zur Georeferenzierung vorgeschlagen; Deckblatt-, Geschoss-, Kanal- und Legendenseiten nur, wenn sie bereits eine Georef haben. Dokumente ohne Lageplan-Seite erhalten einen Eintrag `lageplan_klassifizieren` (Befund Pilot Objekt 404: 13 Seiten statt 1).

- **OSM / Systemdiagnose:** Persistente Geodienst-Zaehler mit Redis und lokalem TTL-Fallback, lesendes Admin-MCP-Tool `admin_osm_pruefen` mit parallelen optionalen Status-/PBF-Proben und OSM-Teilstatus ohne HTTP in der Systemuebersicht. Neuer leichter Check `osm.gebaeude_bestand` fuer fehlende/veraltete Bestaende, Prueflaeufe und Importfehler. BSP-Pflichtregeln dokumentiert; keine neue Verwaltungsseite, keine Migration.

- **Objektkarte / OSM:** Gebaeudeumrisse aus konfigurierbaren lokalen PBF-Extrakten, standardmaessig Vorarlberg; Overpass als Rueckfall ausserhalb der importierten Huellen. Begrenzter Download, Aenderungsvergleich mit manueller Pruefung, CLI und Leader-Import (03:30 Europe/Vienna). Veraltete Daten bleiben analysierbar, sperren aber die Georef-Speicherung. Globale OSM-Tabellen, Migration 0335; neue Abhaengigkeit osmium. Betrieb: [lokaler OSM-Bestand](docs/architecture/osm-gebaeude.md).

- **Objektkarte / Betrieb:** Strukturierte externe Overpass-Fehler mit Codes, Wiederholbarkeit, Korrelations-ID und Endpunktversuchen; UI antwortet weiter mit 422. Cache-Antworten weisen hit/miss aus. Identische offene Planvorschlaege erzeugen keine Aenderung und keinen Audit-Eintrag; reine batch_id-Wechsel bleiben still. GIT_SHA wird bei systemd/venv-Deploys ohne gesetzte Build-Revision aus dem lokalen Repository gelesen. Nur private.coffee bleibt Standard-Fallback, da kumi.systems derselbe Host ist. Keine Migration.

- **Overpass:** Konfigurierbare Ausweichendpunkte bei Ueberlastung und Timeout, mit eigenem Fehlerschutz je Endpunkt. Gebaeudeumrisse haben 25 s je Versuch und 45 s Gesamtbudget und sind vom Hydranten-Breaker entkoppelt. Hydranten-Zeitlimits im Alarmpfad bleiben unveraendert; Strassenabfragen und Planspiel-Import wechseln bei Wiederholungen den Endpunkt. Keine Migration.

- **Objektkarte:** Lesende MCP-BSP-Arbeitsliste fuer fertige aktuelle Lageplan-/Brandschutzplan-Seiten mit priorisiertem naechstem Schritt, Vorversions-Georef und Vorschlags-/Markerzahlen. Symbolkatalog mit Legenden-Aliassen und Richtungsfeldern fuer System- und eigene Symbole. Keine Migration; Upload-Benachrichtigung und vorbelegter Editor-Link bleiben offen.

- **Objektkarte:** Bugfix: MCP-Seitenbilder werden wie im Editor gedreht ausgeliefert, auch im Einsatz und beim Thumbnail-Fallback. Optionaler Seitenausschnitt mit Koordinatenhinweis, Rotation und Bildgroesse. Neues Tool `objekt_karte_georef_uebernehmen` uebertraegt gespeicherte Referenzpunkte innerhalb derselben Dokumentgruppe auf eine neue Planversion; Paarfehler, Leave-one-out und Georef-Pruefung sichern die Uebernahme. Analyse bleibt lesend, Speicherung nur durch die Objektverwaltung bei Klasse A. Keine Migration.

- **Objektkarte:** MCP-Vorschlaege uebernehmen Bildrichtungen und berechnen Kartenrotationen aus der Georeferenz (Pfeil-Nullrichtung Osten). Wiederholte Planlaeufe aktualisieren offene Vorschlaege per Bildtoleranz oder erkennen bereits freigegebene Marker. Laufkennung `batch_id`, Listenfilter und Ergebniszaehler; kompatible Georef-Revisionen berechnen Richtungen neu. Migration 0334.

- **Objektkarte:** Neues MCP-Tool `objekt_karte_georef_zuordnen` ordnet markierte Bildkonturen deterministisch OSM-Gebaeudeecken zu. Leave-one-out und Mehrdeutigkeitspruefung erlauben nur eindeutige Klasse A zur Speicherung durch die Objektverwaltung; Klasse C verweist zur menschlichen Pruefung auf den Georeferenzierungseditor. Keine Migration.

- **Android-Aktualisierung:** Pull-to-Refresh auch auf Login-, Public- und Infoscreen-Seiten; innere Scrollbereiche und echte Formulareingaben werden berücksichtigt, Such-/Filterwerte sperren nicht. Sperrgründe werden bei einer Ziehgeste angezeigt; fehlgeschlagene Resync-Aufträge werden gemeldet.
- **Straßensperren-Infoscreen:** Wetter-Monitorrahmen mit gemeinsamen Farben und Schriftarten; höhenabhängige Listenseiten mit Seitenanzeige statt Scrollleisten. Alle Seiten laufen vor Karte und Einzelansichten durch, Datenaktualisierungen behalten den Fortschritt. Mobil bleibt die vollständige Liste scrollbar. Keine Migration.
- **Objektkarte:** Offene KI-Vorschläge automatisch anzeigen, direkt prüfen und alle unkritischen Vorschläge mit einem Klick in Blöcken zu 30 freigeben. Eine gemeinsame Warnungsbestätigung, atomar Speichern & freigeben nach Positionskorrektur und direkter Link zur offenen Arbeitskopie. Sicherheitsregeln und MCP unverändert; keine Migration.

- **Verwaltungsmenue:** Pro Fachgebiet ein Hauptpunkt, Unterseiten als URL-basierte Tabs aus einem zentralen Katalog (`admin_navigation.py`). Neue Gruppe Systemverwaltung (nur Sysadmin), Organisations-Einstellungen in vier Tabs, Systemstatus mit Tabs. Admin-Sidebar 42 -> 23, Sysadmin 54 -> 34 Eintraege; alle URLs und Rechte unveraendert. Waechter-Test ordnet jede Admin-Seite einem Hauptpunkt zu; Regel in CLAUDE.md und docs/architecture/admin-navigation.md. Keine Migration.

- **Alarm-Link:** Gast-Anmeldung mit Einsatz-PIN und Namen direkt über Teams-/SMS-Link; IP-Limit und einsatzweite Fehlversuchssperre. QR-/Gastzugänge gelten ausschließlich für ihren Einsatz; Alarm-Link-Gastzugänge enden auch bei Ablauf oder Widerruf des Links. Keine Migration.

- **Alarmierung und Druck:** MariaDB-Snapshot-Konflikte (1020) beim Alarm-Claim begrenzt wiederholen; Druck-Deduplizierung liest den konkurrierenden Gewinner unter bestehender Zeilensperre, ohne Fachaenderungen der aufrufenden Transaktion zu verwerfen.

- **Einheitenmodus:** Offline-Outbox prüft den aktuellen Zugang vor jedem Versand, auch bei parallelem Reconnect und Retry. Abgelaufene Zugänge behalten Einträge lokal als `blockiert_zugang`, ohne vorherigen POST.

- **Admin-MCP:** Handlungsorientierte Login-Hinweise erst nach Passwortnachweis, geheimnisfreie Audit-Gruende und Anzeige in der Systemdiagnose. Fehlender OAuth-Scope verwendet `mcp-admin`; zusaetzliche Discovery-Aliase und lokale ChatGPT-OAuth-Regressionstests. Replay-Schutz und Sperren unveraendert; Login-/CLI-Checklisten im Wiki, keine Migration.

- **Systemadministration:** Phase 6: Systemdiagnose-Cockpit mit mobilen HTMX-Karten, gemeinsamen Diagnose-/Reparaturdiensten, GitHub-CI, Codex-Auftrag und maskiertem Audit. Optionale Tagesdiagnose (Default AUS), globale DB-Claims und deduplizierte System-Sammelmail; Migration 0329. Ausfuehrliche Betriebs- und Recovery-Dokumentation im Wiki.

- **Systemadministration:** Phase 5b: GitHub-Lesetoken verschluesselt in den Systemeinstellungen verwalten, testen und rotieren; MFA-Schutz, Ablaufhinweise, tokenfreie MCP-Zugangsstatus und Diagnosewarnungen. Umgebungsvariable bleibt Fallback; keine Migration.

- **Systemadministration:** Phase 5 des Admin-MCP: lesende lokale Codekorrelation, GitHub-CI-/Deploy-Abgleich und ausschliesslich als Text erzeugte Codex-Auftraege. Optionales GitHub-Lesetoken, gemeinsame Drosselung, geheimnisfreie Metadaten und leichter Check `deploy.version`; keine Migration.

- **Systemadministration:** Kontrollierte Reparatur-Engine mit drei festen Wartungsaktionen, Dry-Run, Datenbestand-Hash, Idempotenz und globalem DB-Mutex. Fuenf Admin-MCP-Werkzeuge, standardmaessig AUS und nur mit aktiver 2FA; aktionsgebundene Web-Freigaben fuer HIGH. Migration 0328.

- **Systemdiagnose:** Registrierbare Integritaetschecks, begrenzte globale Diagnose-Laeufe mit deduplizierten Befunden und vier zusaetzliche Admin-MCP-Werkzeuge. Einsatz-Drosselung, atomare Claims und Retention; keine Fachdatenmutationen oder Reparaturen. Migration 0327.

- **Systemadministration:** Separater lesender System-Admin-MCP unter `/mcp/admin` mit sieben Diagnosewerkzeugen, eigenem OAuth, optionaler TOTP-/Recovery-Anmeldung und expliziten globalen/persoenlichen Freigaben (Default AUS). Migration 0326.

- **Systemadministration:** Optionale TOTP-2FA (Standard AUS) mit Recovery-Codes, zentraler Session-Pruefung, Widerruf und lokalem CLI-Notfallreset. Aktivierung erst nach Einrichtung aller aktiven Systemadministratoren.

- **SMS-Gateway und Windows-Druckagent:** Synchrone DB-Aufrufe blockieren die WebSocket-Event-Loop nicht mehr. SMS-Reconnects ersetzen alte Sockets desselben Tokens; Idle-Pings und ein Tot-Timeout bereinigen verwaiste Verbindungen. Trennungslogs enthalten Close-Code, Grund und Dauer. Neuer konfigurierbarer Loop-Lag-Monitor und Gunicorn-Worker mit 60 Sekunden WebSocket-Pong-Timeout erleichtern Diagnose und stabilisieren Keepalive.

## 2026.10.23 (2026-10-23)

### Verbesserungen und Fehlerbehebungen im Einsatz

- **Einsatzboard:** Spalten-Umbenennen lädt das Kanban neu und zieht die Tabs nach; Ausrückordnung mit farbigem Fahrzeugstatus; das Detailfenster dunkelt die Oberfläche nicht mehr dauerhaft ab; Spaltennamen mit Tooltip.
- **LIS:** Fahrzeugstatus wird nur bei Änderung gesendet, mit begrenztem Retry und Schutzfenster gegen Rückkopplung beim Poll.
- **Objekte:** Objektauswahl beim Anlegen eines Einsatzes; bei Meldung und Auftrag lassen sich Objektdokumente wie beim Bearbeiten übernehmen. Objektinformationen und Gefahren fließen in KI-Auftragsvorschläge, Lagehinweise und Lagebild ein.
- **Großschadenslage:** Neue Einsatzstellen erscheinen live im Board; einheitliche Prioritätsfarben mit Legende auf der Lagekarte; Einheitenstatus wie Fahrzeugstatus (Rot/Orange/Grün), standardmäßig nur eigene Einheiten; Bürgerportal-Link nur nach Bestätigung erneuern; größerer Status-Button.
- **Tablet und Einheitenansicht:** Ladefehler sichtbar, robuster Start, Schnellaktionen (Link, SMS, QR) in der Kräfteübersicht. Einheiten-Tablets öffnen die Lage zuerst in der Einheitenansicht, der Wechsel zur Gesamtansicht erfolgt per Link.
- **Archiv und PDF:** Einsatz-PDF und GSL-Bericht vollständiger (Seitenumbruch, Verläufe, Objekt); Gesamt-PDF der Großschadenslage im Archiv.
- **Gerätezugang:** Ungültige Gerätezugänge werden erkannt, aggregiert protokolliert und in der Geräteverwaltung angezeigt (Fehlversuche, Grund, Gerätetyp, Filter); rückwirkende Auswertung per Skript.
- **App:** Pull-to-Refresh-Schnittstelle, Verbindungsstatus-Banner, Ping-Intervall-Leak behoben. Die Android-App 2026.10.23 bringt natives Pull-to-Refresh und stoppt Wiederholungen bei ungültigem Gerätezugang.

### Wetter 2.0

- Open-Meteo- und GeoSphere-Provider, zeitstempelbasierte Summen und Böenmaxima, Warnstatus mit Datenqualität, Kennzahlen mit Horizonten 1/3/6/12/24 h, Radar-Animation, Modellvergleich, gemeinsamer Cache und Admin-Diagnose.

### Einsatz- und Katastrophenpläne

- Planart, Prüfzyklus mit verantwortlicher Person, rollenbezogene Maßnahmen mit Akteuren, zentrale Kontakte, Quellennachweise bis zur PDF-Seite; Maßnahmen als Auftrag oder Meldung in den Einsatz übernehmen; Plan-Suche, Lightbox, neue Startseite; Anlegen per MCP.

### Suche

- Kontakte und Pläne (Planart, Akteure, Links) in der zentralen Suche; semantische Suche mit Vektorstufe.

### Weitere Korrekturen

- Abgerückte Einheiten in der Kräfteübersicht reaktivierbar; Lesewege für Objekte, Dokumente und Kontakte für alle Einsatzrollen; KI-Antwort ohne ThinkingBlock stürzt nicht mehr ab.

### Hinweise zum Update

- Neue Datenbank-Migrationen 0302–0305 (Planart, Prüfzyklus, Akteure, Geräte-Fehlversuche): `alembic upgrade head` ausführen, beim In-App-Update geschieht das automatisch.
- Die Android-App 2026.10.23 ist separat veröffentlicht; Gerätetests stehen noch aus.

### Details

- **ECP-Verbesserungen (Oktober 2026)**, Details je ID in `docs/plans/ecp-verbesserungen-2026-10.md`:
  - **Einsatzboard**: Spalten-Umbenennen lädt das Kanban neu und zieht Lane-Tabs nach,
    Diagnoseskript `scripts/spalten_diagnose.py` (ECP-02, Ursache am Gerät nicht reproduziert);
    Spaltennamen mit Tooltip (ECP-03); Ausrückordnung mit farbigem Fahrzeugstatus und Zeit
    (ECP-04); Detail-Modal lässt keinen dunklen Backdrop mehr zurück (ECP-06, gehärtet).
  - **LIS**: Fahrzeugstatus nur bei Änderung, begrenzter Retry, 30-s-Schutzfenster gegen
    Poll-Rückkopplung, Audit `lis.status_pushed` (ECP-05).
  - **Objekte**: Objektauswahl beim neuen Einsatz; bei Meldung und Auftrag werden
    Objektdokument-Seiten wie beim Bearbeiten übernommen, statt das Objekt zu verknüpfen
    (ECP-01). Objektinfos und Gefahren fließen in KI-Auftragsvorschläge, Lagehinweise und
    Lagebild ein, Gefahren werden deterministisch hervorgehoben (ECP-07).
  - **GSL**: Neue Einsatzstellen live im Board, einheitliche Prioritätsfarben und Legende
    auf der Lagekarte (GSL-01/02); Einheitenansicht robuster mit sichtbaren Ladefehlern und
    Zugang-Schnellaktionen in der Kräfteübersicht (GSL-03); Bürgerportal-Link nur nach
    Bestätigung erneuern (GSL-04); größerer „Status eintragen“-Button (GSL-05);
    Einheitenstatus wie Fahrzeugstatus (GSL-06) und standardmäßig nur eigene Einheiten
    (GSL-07). Einheiten-Tablets öffnen die Lage zuerst in der Einheitenansicht mit Wechsel
    zur Gesamtansicht (Cookie 12 h).
  - **PDF/Archiv**: Einsatz-PDF und GSL-Bericht vollständiger mit Seitenumbruch, Verläufen
    und Objektdaten (DOC-01); Gesamt-PDF der Großschadenslage im Archiv; Archivliste läuft
    auch unter SQLite.
  - **Gerätezugang**: Ungültige Gerätezugänge werden mit Code (`device_revoked`,
    `device_unknown`, `user_inactive`) beantwortet und aggregiert protokolliert, Geräteverwaltung
    mit Fehlversuchen, Grund, Gerätetyp und Filtern (SEC-01/02/04, Migration 0305);
    rückwirkende Auswertung per `scripts/geraete_zugriffe_auswerten.py` (SEC-03).
  - **App**: Pull-to-Refresh-Schnittstelle, Verbindungsstatus-Banner, Ping-Intervall-Leak
    behoben (APP-01/02). Android 2026.10.23: natives Pull-to-Refresh, Stopp der
    Wiederholungen bei ungültigem Zugang; Gerätetests offen.

- **Wetter 2.0 (Phasen 1–3)**: Open-Meteo-Client, GeoSphere-Modell, konfigurierbare
  Providerreihenfolge, zeitstempelbasierte Prognosesummen und Böenmaxima, Warnstatus mit
  Datenqualität ohne Entwarnung bei Ausfall.

- **Wetter Phase 6**: Gemeinsame Radar-Animation für Wetterseite und GSL-Lagekarte,
  einzeln schaltbare Wetterebenen, Windpfeil am Fokuspunkt und gelieferte Warnpolygone.
  Modellvergleich lädt beim Öffnen, vergleicht vollständige gemeinsame Stundenfenster
  und zeigt absolute Abweichungen ohne Alarmwirkung. Admin-Diagnose mit Providerzustand,
  Cache-Modus und Betriebswerten ohne Zugangsdaten; neue `WEATHER_COMPARE_WARN_*`-Schwellen.

- **Wetterbetrieb**: Optionaler gemeinsamer Redis-JSON-Cache mit Worker-Sperren,
  lokalem Fallback bei Ausfall und getrennten Kachelmann-Caches je Organisation.
  Eigene TTL für aktuelle Daten, begrenzte HTTP-Wiederholungen, strukturierte
  Providerwechsel-Logs ohne Zugangsdaten und flüchtige Gesundheitszähler.

- **Wetterdarstellung**: Aktuell, Kurzfristig (1/3/6 h), Prognose (12/24 h und Tag)
  und amtliche Warnungen getrennt. Böenspitzen mit Org-Lokalzeit und km/h,
  Datenstand mit Quelle, Modell, Alter, Frische und Ersatzquelle; unvollständige
  Fenster werden gekennzeichnet. Board/GSL und Infoscreen zeigen Kurzprognosen,
  öffentliche JSON-Endpunkte erhalten zusätzliche Prognose- und Datenstandsfelder.

- **Wetter**: Kennzahlen für Regen, Sturm, Schnee/Glätte, Gewitter und Hochwasser-Kontext;
  Prognosefenster 1/3/6/12/24 Stunden, Spitzen mit Zeitpunkt und keine Bewertung
  bei unvollständigen Daten. Gemeinsame Hinweisschwellen und km/h-Templatefilter;
  Schwellen der Alarm-Engine bleiben unverändert.

- **Pläne – Bedienung**: Maßnahmen lassen sich als Auftrag oder Meldung in einen Einsatz
  übernehmen, Lightbox statt neuem Fenster, Aktionslinks im Fahrtenbuch-Design, neu
  geordnete Planstartseite, Plan-Suche über die zentrale Hybrid-Engine; Suchindex enthält
  Planart, Akteure und Verknüpfungen.

- **Einsatz- & Katastrophenpläne** (bisher „Katastrophenpläne“, neue Adresse `/plaene`,
  alte Links leiten weiter):
  - Neue **Planart**: Alarm- und Einsatzplan, Maßnahmenplan, Katastrophenplan, mit Kapitel-
    und Phasenvorlagen (Migration 0302).
  - **Prüfzyklus**: verantwortliche Person, Intervall je Planart, „Überprüfung bestätigen“
    ohne neue Version, Erinnerung vorab/fällig und Eskalation an Freigeber (Migration 0303).
  - **Akteure**: Maßnahmen getrennt für Feuerwehr, Netzbetreiber, Behörde usw., Filter und
    Gliederung nach Akteur, PDF-Auszug je Akteur, erweiterbarer Katalog (Migration 0304).
  - **Zentrale Kontakte statt Telefonnummern im Text**: Erkennung in allen Feldern,
    Assistent zum Verknüpfen oder Anlegen, Verständigungsliste nach Akteur.
  - **Quellennachweise bis zur PDF-Seite**: Seitenvorschau, Original-PDF auf Seite,
    Seitenauswahl im Editor, Quellenverzeichnis im PDF, Hinweis „Maßnahme ohne Quelle“.

- **MCP**: `katplan_anlegen` legt Pläne als Entwurf mit Kapitel-/Phasenvorlage, gemeinsamer UI-Validierung und optionalem Trockenlauf an.

- **Semantische Suche**: Optionale lokale Ollama-Embeddings mit `embeddinggemma`,
  Vektortabellen je Modell/Dimension, eigener Vektor-Outbox und Distanzschwellwert;
  Evaluationsskript mit Recall/MRR je Fragengruppe und Review-CSV sowie Betriebsdoku.
  Die Eval-Messung auf dem Testsystem steht noch aus.

- **Suche**: Kontakte in der zentralen Suche (UI und MCP `ecp_suchen`) mit den Rechten
  des Kontaktmoduls: Verzeichnisrollen finden alle aktiven Kontakte (auch per
  Telefonnummer, Anruf-Link im Treffer), übrige Einsatzrollen nur die für den Einsatz
  freigegebenen Kontakte mit Dienstnummern. Kontakte werden live geprüft, nie indexiert;
  MCP liefert keine Telefonnummern.

- **Korrekturen**: Abgerückte Einheiten in der Kräfteübersicht reaktivierbar; Objekt-,
  Dokument- und Kontakt-Lesewege für alle Einsatzrollen; KI-Antwort ohne ThinkingBlock
  stürzt nicht mehr ab, DB-Session wird vor Medien-Streaming freigegeben.

## 2026.10.21 (2026-10-21)

### Katastrophenpläne (neues Modul)

- Arbeitsstand und unveränderliche Freigaben mit Versionen, Kapitel, Phasen und Elementen; Verortung auf der Plankarte, Verknüpfungen zu Kontakten, Objekten und Wasserstellen, Anhänge.
- Versionierte PDF-Planquellen mit Vergleich, KI-Import mit Review, Qualitätsstatus, Wiedervorlage und lesbare PDF-Offlinefassung mit Verzeichnis.
- Adresssuche im Editor mit dem Ort der Organisation als Vorauswahl.
- MCP: Pläne lesen und durchsuchen, Entwürfe anlegen und ändern, Verknüpfungen setzen, Quellseiten und Bilder übernehmen, Serienverortung und Abgleich des Arbeitsstands mit einer Quelle.

### Zentrale Suche

- Suche über die Lupe, `/` oder Strg+K: Objekte, Objektdokumente, Katastrophenpläne, Einsätze, Straßensperren, Wasserstellen und GSL-Daten mit Rechteprüfung, im Einsatz und in der GSL auf den jeweiligen Kontext bezogen.
- Org-Schalter, Indexstatus und Neuaufbau in der Verwaltung; MCP-Werkzeug `ecp_suchen`.

### Navigation und Oberfläche

- Module nach Einsatznutzen gruppiert: Im Einsatz (Katastrophenpläne, Nachschlagewerke, Kontakte, Straßensperren, Förderstrecke), Einsatzbegleitend (Fahrtenbuch, Atemschutz-Geräteprüfung, UAS), Verwaltung & Planung (Objekte, Probenplan, Planspiele, Mailing), Auswertung (Statistik). Gleiche Reihenfolge im Desktop-Menü, im Mobil-Menü und auf der Übersicht.
- Kopfzeile aufgeräumt: Profil ganz rechts mit Hilfe-Link, Archiv ans Ende. Mobil sind die Kontoaktionen nur noch im Profil, die untere Leiste hat jetzt „Menü“.
- Einheitliche Icons (Tabler) statt Emoji in der gesamten App, auf öffentlichen Seiten, Infoscreens, PDFs und Ausdrucken.
- Gemeinsame Personenauswahl für Fahrtenbuch und Atemschutz-Geräteprüfung, Atemschutzformular für Handy und Tablet.

### Leitstellennummer (GSL)

- Einsatzstellen-Detail, GSL-Druck, Suche und MCP verwenden ausschließlich die echte Leitstellennummer.

### Dokumentation

- Wiki-Startseite nach Menüstruktur neu aufgebaut, mit Einstieg nach Aufgabe und Seitenleiste auf jeder Seite; neue Seiten zur Atemschutz-Geräteprüfung; README entsprechend gegliedert.

### Technik

- MariaDB 12.3 für Docker, E2E und CI.
- Aktualisierte GitHub Actions und Abhängigkeiten.

### Update-Hinweis

- Neue Datenbank-Migrationen 0294–0301 (Katastrophenpläne, Suchindex, Icon-Namen): nach dem Update `alembic upgrade head` ausführen, beim In-App-Update geschieht das automatisch.

### Details

- **Navigation**: Module nach Einsatznutzen gruppiert (Im Einsatz, Einsatzbegleitend,
  Verwaltung & Planung, Auswertung), einheitlich in Desktop-Menü, Mobil-Menü und
  Übersichtskacheln aus einer Quelle (`app/services/modul_navigation.py`). Profil ganz
  rechts mit Hilfe-Link, Archiv ans Ende; mobil Kontoaktionen nur noch im Profil,
  Bottom-Nav mit „Menü“ statt Verwaltung/Archiv.
- **Icon-System**: Einheitliche Tabler-Icons statt Emoji in Navigation, Board, GSL,
  Karten, Verwaltung, öffentlichen Seiten, PDF und Druck; Merkmal-Symbole in der
  Datenbank (Migration 0301), Wächtertest gegen neue Emoji.
- **Formulare**: Gemeinsame Personenauswahl für Fahrtenbuch und Atemschutz-Geräteprüfung,
  responsives Atemschutzformular.
- **Dokumentation**: Wiki-Startseite nach Menüstruktur neu aufgebaut, Seitenleiste,
  neue Seiten zur Atemschutz-Geräteprüfung; README entsprechend gegliedert.

- **Zentrale Suche**: Organisationsweite und kontextbezogene Suche über freigegebene
  Fachinhalte und Live-Daten mit Rechteprüfung. Org-Schalter, Indexstatus und
  asynchroner Neuaufbau, MariaDB-FULLTEXT-Test und Betriebsdokumentation ergänzt.

- **Katplan-MCP – Quellenabgleich**: Neues lesendes Werkzeug für Bearbeiter meldet
  Seiten ohne Element, Elemente ohne Quelle, veraltete Quellbezüge, mögliche Dubletten
  und offene Vorschläge je Seite mit nächsten Schritten. Vorschlagsimporte warnen vor
  ähnlichen bestehenden Elementen, ohne die Review-Queue zu blockieren.

- **Katplan-MCP – Serienverortung**: Bis 30 Elemente mit Ortskandidaten aus verknüpften
  Objekten/Wasserstellen, Adresse und Titel; begrenztes Suchgebiet um die Organisation,
  stabile Auswahl-IDs und Duplikatzusammenfassung. Ausgewählte Punkte bleiben ungeprüft,
  geprüfte Orte sind vor unbeabsichtigtem Überschreiben geschützt; Trockenlauf mit Rollback.
  Mehrdeutige Adressen werden zur Auswahl angeboten. Entwürfe können Stammdatenpunkte
  über `geometrie_von` übernehmen.

- **Katplan-MCP – Quellseiten und Bilder**: Bis 50 Quellzuordnungen mit optionalem
  Versionsschutz, Teilfehlern und strengem Rollback. Quellreferenzen enthalten geschützte
  Seiten-, Text- und Vorschau-URLs mit Freigabebindung. Eingebettete Bilder lassen sich
  mit Vorschauen lesen; Seitenbilder, Einzelbilder und Ausschnitte als Anhänge übernehmen.
  Trockenläufe hinterlassen weder Anhänge noch Bilddateien.

- **Katplan-MCP**: Zahlen in Typdaten bleiben als Text erhalten; verworfene Werte werden
  mit Pfad gemeldet. Entwürfe liefern das gespeicherte Element und die wirksamen Felder,
  unterstützen `streng` und einen rückstandsfreien Trockenlauf mit `pruefen`.
  Adressen, Geometrie und Quellbezug lassen sich explizit leeren; Leseantworten des
  Arbeitsstands liefern die zum Schreiben benötigte `version` und `plan_version`.
  Verknüpfungen und Elementbeziehungen lassen sich idempotent setzen und entfernen;
  Plan-Stammdaten unterstützen Teiländerungen mit Versionsschutz und Trockenlauf.
  Schreibantworten beachten die Telefonrechte; die Qualitätsprüfung meldet Telefonnummern
  im Beschreibungstext trotz Kontaktverknüpfung.

- **Katastrophenpläne**: Arbeitsstand und unveränderliche Freigaben, Kapitel/Phasen,
  Karte, Suche, Kontakte und Anhänge, versionierte PDF-Quellen mit Vergleich,
  KI-Import mit Review, MCP, Qualitätsstatus und PDF-Offlinefassung ergänzt.
  Rollen und Modulschalter, Wiedervorlage und Wiki-Dokumentation; Migrationen 0294–0299.
  PDF-Offlinefassung überarbeitet: Typdaten als Checkliste und Tabellen statt JSON,
  Silbentrennung statt Wortbruch, Verzeichnis mit Seitenzahlen. Adresssuche im Editor mit
  Suchfeld, Org-Ort als Vorauswahl und Übernahme in die Adressfelder. MCP-Entwürfe nehmen
  eine typisierte Adresse an und können sie mit `verorten` als ungeprüften Punkt setzen.

## 2026.10.19 (2026-10-19)

### Planspiele (neues Modul)

- Übungsgenerator für Einsatzboard und Großschadenslagen: Szenariokatalog, Ortswahl mit OSM-Import (robust mit Timeout und Wiederholung), Ereignis-Engine mit Regie-Cockpit, GSL-Unwettergenerator, Allein-Betrieb mit Auswertung.
- Ortschaft-Filter, abwechslungsreichere GSL-Meldungen, wiederverwendbare Vorlagen, Design wie das Fahrtenbuch, Kachel auf der Übersicht.

### Großschadenslagen (GSL)

- Erkundungsablauf im Einheitenmodus: Erkundet → disponieren, Kommentar und Foto, nächster Auftrag mit Adresse; Einheiten werden nach Abschluss automatisch von der Einsatzstelle abgezogen.
- Lagemeldungs-Timer startet erst, wenn eine Einheit übernommen hat bzw. vor Ort ist.
- Zeitreise mit Filtern (neueste oben), Abschnitte-Grid, Disposition nach Abschnitten gruppiert.
- Neues Einsatzstellen-Blatt mit automatischem Druck bei Disposition; Druckregel „Einsatz angelegt“ getrennt nach normal und GSL.
- Login mit verknüpftem Mitglied öffnet für Gruppenkommandanten direkt die Einheitenansicht („Meine Einheiten“).
- iPhone-Foto-Upload im Einheitenmodus repariert; Nummernabfrage nur ohne hinterlegte Mobilnummer.

### Einsatzboard und Alarmierung

- Abgeschlossene Einsätze sind im Board nur noch lesbar; Änderungen werden serverseitig gesperrt.
- Einsatzinfo-Mail als zusätzlicher Kanal analog zur SMS-Einsatzinfo, mit HTML-Layout, Karte und Vorschau.
- Ausschlussbegriffe je Organisation (z. B. „bereits erledigt“): keine SMS, Teams, Mail und Push.
- DIBOS: Einsätze nach automatischer Wiedereröffnung wieder automatisch schließen.
- SMS-Einzelversand wird auf mehrere Android-Gateways verteilt.
- Funkjournal, Einsatzleiter-Wechsel und Atemschutz-Übersicht aktualisieren sich ohne Neuladen der Seite, auch für andere Nutzer; ein frei eingegebener Einsatzleiter wird in der Kopfzeile angezeigt; Funkjournal mit Suchfeld.

### Druck

- Druckregeln je Alarmstufe mit Abweichungen, Objekt-Inhalten und Übersichtsmatrix.
- Windows-Druckagent: Lease-Zustellung, Redundanz und sichere Druckübergabe; Agent-Typ, Inventar, Routen mit Priorität und Bereitschaftsanzeige im Druckdialog.
- Druckregel „E-Mail eingegangen“; Mail-Zuordnung auch über die Leitstellennummer.

### Benutzer, Kontakte und Mail

- Einladung per gebrandeter Mail statt Klartext-Passwort, App-Links (inkl. Auto-Update-APK), SSO-Hinweis beim Passwort-Reset, Bestätigung nach Passwortänderung, Mail-Vorschau mit Testversand.
- Benutzer lassen sich mit Mitgliedern verknüpfen; globale Eindeutigkeit wird vor der Anlage geprüft.
- Kontakte: gefilterte Einsatztelefonlisten und Verzeichnisse mit Druckvorschau, PDF und Organisationslogo; Leser und Einheiten sehen nur Einsatzkontakte.
- Mail-Eingang: Freigabeliste mit mehreren Zeilen; „Alle“ ohne Auth-Pflicht.

### KI-Anbindung (MCP-Server)

- Einsätze lesen (Einheiten, Aufträge, Meldungen, Verlauf, Objekte, Downloads) sowie Aufträge und Meldungen anlegen.
- GSL vollständig: Lagebild lesen, Einsatzstellen führen, Journale, Bürgermeldungen, Medien herunterladen und Fotos hochladen.
- Objektdokumente lesen (Seitenbild und OCR), PDF-Übergabe aus ChatGPT.
- Benutzer und Mitglieder verwalten und sicher abgleichen, `meine_einheiten`.

### Technik und Sicherheit

- Event-Loop entlastet: 522 Routen mit Datenbankzugriff laufen jetzt im Threadpool statt auf der Event-Loop, sodass WebSockets, Alarmierung und DIBOS-Polling nicht mehr durch langsame Abfragen gebremst werden. Wächtertests verhindern Rückfälle.
- SSO-Token-Prüfung mit PyJWT statt des kaum noch gepflegten python-jose; strengere Ruff-Prüfungen.
- CSP-Messmodus (`CSP_REPORT_ONLY`) als Vorstufe für eine strengere Content-Security-Policy.
- Frontend-Bibliotheken über npm nachvollziehbar und von Dependabot überwacht; aktualisiert u. a. Alpine 3.17, htmx 2.0.11, Konva 10, Geoman 2.20 (behebt lodash-Sicherheitsmeldung).
- Objektliste mit Spalte „Aktualisiert“, Datumsfilter und Sortierung nach allen Spalten; diverse Layout-Korrekturen (Einsatzinfo auf Tablet-Breite, Abschnitts-Badges, Nur-Ansicht-Banner).

### Hinweise zum Update

- Datenbank-Migrationen 0279–0293: `alembic upgrade head` ausführen.
- Beim Update „Abhängigkeiten installieren“ aktivieren (neu: PyJWT, entfernt: python-jose, apscheduler). PyJWT ist meist schon über andere Pakete vorhanden.
- Neu in `.env` (alle optional): `INVITE_TTL_HOURS` (Gültigkeit von Einladungslinks, Standard 168), `ANDROID_APP_REPO` und `ANDROID_APK_PRAEFIX` (App-Links/Auto-Update-APK), `CSP_REPORT_ONLY` (Standard `false`). Siehe `.env.example`.
- Kein Node.js auf dem Server nötig; die Frontend-Dateien sind im Release enthalten.
- Nach dem Update kurz prüfen: SSO-Login, PIN-/Passwort-Login, ein Probealarm über das Gateway, eine Foto-Annotation mit Text (Konva 10).

### Details

- **Technik/Modernisierung**: 522 Routen mit synchroner DB-Session auf `def` im
  Threadpool umgestellt; Async-Brücke und Wächtertests verhindern neue Event-Loop-Blockaden.
  Ruff um `ASYNC`, `DTZ`, `B`, `S110` und `T20` mit Bestandsbaseline erweitert,
  `apscheduler` entfernt, Docker-Frontend auf `node:22` und GitHub Actions aktualisiert.
- **Bedienung**: Funkjournal, Einsatzleiter-Wechsel und Atemschutz aktualisieren sich
  nach HTMX-Aktionen ohne Vollreload; Freitext-Einsatzleiter in der Topnav und
  Funkjournal-Suche nach Einheit, Kanal und Inhalt ergänzt.
- **Sicherheit**: SSO-ID-Token-Validierung von `python-jose` auf `PyJWT[crypto]`
  umgestellt; optionaler CSP-Messmodus (`CSP_REPORT_ONLY`) mit aggregierten Berichten
  über `/csp-report` ergänzt.
- **Frontend-Bibliotheken**: Versionen zentral in `package.json`, reproduzierbares
  Vendoring mit CI-Prüfung und Dependabot für npm, pip und Actions eingerichtet.
  Geoman 2.20.2, Alpine 3.17.4, htmx 2.0.11, Konva 10.9.0 (bisherige Textdarstellung),
  Yjs 13.6.33, lib0 0.2.119 und y-websocket 3.1.0 aktualisiert;
  quill-cursors bleibt wegen der ESM-Umstellung ab 6.x auf 5.0.0.

- **Kontakte**: Gefilterte Einsatztelefonlisten und Kontaktverzeichnisse mit Druckvorschau,
  PDF-Download, Browserdruck, persönlichen Profilen und optionalem Organisationslogo ergänzt.
  Kategorien und Druckkennzeichnungen sind über Oberfläche und MCP pflegbar (Migration 0293).

- **Benutzer-Mails**: Gebrandete Einladungen statt Klartext-Passwörtern, App-Links,
  SSO-Hinweis beim Passwort-Reset, Bestätigung nach Passwortänderungen und sichere
  Admin-Mail-Vorschau mit Testversand ergänzt (Migration 0291).

- **Benutzer**: Benutzer lassen sich mit einem Mitglied verknüpfen (Migration 0290). Führt das Mitglied in einer aktiven Großschadenslage eine Einheit, öffnet der normale Login die Einheitenansicht dieser Einheit; die Startseite zeigt „Meine Einheiten“.
- **MCP**: Werkzeuge für Mitglieder (lesen ab `fahrtenbuch_admin`, pflegen ab `admin`) und Benutzer (nur `admin`, gleiche Schutzregeln wie die Benutzerverwaltung) sowie `meine_einheiten`; `mcp_whoami` zeigt Mitglied und geführte Einheiten.

- **MCP**: Einsatzleiter, Schriftführer und Admins können Aufträge und Meldungen im sichtbaren, offenen Einsatzboard anlegen; Erstellung, Fachverlauf und Live-Aktualisierung entsprechen dem Board.

- Gateway-Druckregel „E-Mail eingegangen“ mit sicherem Deckblatt und optionalen PDF-/Bildanhängen ergänzt.

- Neu: MCP-Assistenten können sichtbare Einsätze datensparsam lesen, einschließlich Lagebild, Einheiten, Aufträgen, Meldungen, Verlauf und verknüpften Objekten; freigegebene Objekt-Dokumente sowie Auftrags- und Meldungsmedien werden über kurzlebige, live geprüfte Download-Links bereitgestellt.

## 2026.10.12 (2026-10-12)

### Großschadenslagen (GSL): Einheitenmodus und Gruppenkommandanten

- Fahrzeug-Tablets erhalten einen eigenen Einheitenmodus: Aufträge empfangen, Status melden, Lagemeldungen, Fotos und Funkjournal, auch offline mit eigener Warteschlange.
- Ressourcenkarte je Einheit mit Gruppenkommandant (Telefon, Stellvertreter), Journal und Einsätzen. „Einheit hinzufügen“ erfasst Funkruf, Status, Abschnitt, Bereitstellungsraum, Gruppenkommandant und Stärke in einem Schritt.
- Persönlicher, widerrufbarer Zugangslink für Gruppenkommandanten sowie QR-Zugang mit A4-Ausdruck über den Standard-Druckdialog (optional mit SMS-PIN). Die Mobilnummer wird per SMS-Code bestätigt; Auftrags-SMS (neu, geändert, zurückgezogen) mit eigenem Übungsschalter.
- Automatisches Einsatzboard: Erkundung/Abarbeitung folgt der Phase, „Fertig gemeldet“ und Einsatzabschluss. Einheitenkarte mit nächstem Auftrag, Navigation und eigenem Standort; mobile Führungsübersicht mit Aufmerksamkeitsliste und Kartenfiltern.
- Einsatzstellen anlegen mit Karte, Adresssuche und Adressprüfung; ein gesetzter Pin füllt Straße, Hausnummer und Ort. Abschnitte zeigen ihre Einsatzstellen mit Status und Einheiten.
- Material vor Ort: an Einsatzstellen belassenes Material wird als Verleih geführt (Board, Tablet, Verleih-Ansicht).

### Straßensperren (neues Modul)

- Sperren mit Karte, Zeichnen und Adressabschnitt verwalten; der Abschnitt wird im OSM-Straßennetz ermittelt und mit Qualität bewertet. Behördliche Verordnungen als PDF anhängen, Entwurf direkt aus dem PDF.
- Verlängerungen und ersetzende Verordnungen werden erkannt statt als Dublette angelegt.
- Freigabe an Nachbarwehren, öffentliche Freigabelinks, Statusseite und Infoscreen mit widerrufbaren Zugängen.
- Microsoft-Teams-Meldungen mit Adaptive Card und Kartenbild über eine Warteschlange mit Wiederholungen.
- Optional: Anfahrtsprüfung je Einsatz gegen aktive Sperren (ORS/OSRM), Anzeige in Einsatzinfo und Alarm-Infoscreen. Standardmäßig aus.

### Mail-Eingang

- Eigene Empfangsadresse je Organisation über Resend sowie Abruf per IMAP oder Office 365. Eingehende Mails landen im Posteingang und werden passenden Einsätzen als Meldung zugeordnet (auch nachträglich).
- Weiterleitungen mit Originalabsender und Druckregel „E-Mail eingegangen“ mit Deckblatt und Anhängen.

### KI-Anbindung (MCP-Server)

- Neue Werkzeuge für Straßensperren, GSL-Ressourcen und Einheiten-Disposition, Wasserstellen, Organisationsdaten, Original-Objektdokumente und strukturierten Kontakt-Import. Zugangstokens, Links und PINs werden nie ausgegeben.

### Weitere Verbesserungen

- PIN-Login findet Mobilnummern unabhängig von nationaler oder internationaler Schreibweise.
- LIS: Neuanlage scheitert nicht mehr an doppelten Alarm-Aufträgen; manuell wiedereröffnete Einsätze werden von LIS/DIBOS nicht erneut geschlossen.
- Tablet: Upload- und Speicherfehler werden sichtbar angezeigt.
- Administration: Rechtevergabe abgesichert, Rollen `org_admin` und `admin` zusammengelegt, Benutzer lassen sich wieder endgültig löschen.
- Objekte: Einsatzdruck bleibt bei Sammelzuweisung erhalten, Dokumentseiten lassen sich einzeln bearbeiten.
- Kontakte: strukturierte Daten im Browser bearbeiten und sicher importieren; Kontaktformular fällt auf Resend zurück, wenn SMTP fehlt.

### Hinweise zum Update

- Datenbank-Migrationen 0257–0278: `alembic upgrade head` ausführen.
- Neu in `.env`: `GSL_ZUGANG_KEY` (empfohlen; leer leitet die Zugangslinks aus `SECRET_KEY` ab, eine Rotation entwertet sie dann), `MAIL_INBOUND_DOMAIN`, `MAIL_ZUORDNUNG_LOOP_*` sowie optional `EINSATZ_ROUTING_*` (standardmäßig aus). Siehe `.env.example`.

### Details

- Resend-Posteingang je Organisation mit signiertem Webhook, sicherem Abruf und Aufbewahrung ergänzt.
- Resend-Eingang ordnet wartende, freigegebene Mails innerhalb des Rückblickfensters automatisch neu angelegten Einsätzen zu; Posteingang-Zähler und Live-Aktualisierung ergänzt.

- **GSL**: Ressourcenkarte auf Übersicht, Einsätze, Journal und Zugang fokussiert; Personal, Ausstattung und Verbandsaktionen sind nicht mehr über die Oberfläche verlinkt. Abschnittsnamen und QR-Vorschau wurden korrigiert und mobil robuster gestaltet. Die Karte lag bisher unter der oberen Navigation (Kopfzeile, Reiter und Zugang-Tab waren verdeckt); sie liegt jetzt darüber.

- **GSL**: „Einheit hinzufügen“ erfasst Funkruf, Status, Abschnitt, Bereitstellungsraum, Gruppenkommandant mit Telefon und Stärke in einem Vorgang.
- **GSL**: QR-Zugang für Einheiten mit A4-Ausdruck (Token `gkq_`, optional mit PIN) und neuer Druckregel „GSL – Neue Einheit / QR-Einheitenzugang“; Zugangslinks sind jetzt aus `GSL_ZUGANG_KEY` ableitbar und müssen nicht neu ausgegeben werden (Migrationen 0268–0270).
- **GSL**: Gruppenkommandant bestätigt seine Mobilnummer per SMS-Code; automatische Auftrags-SMS (neu, geändert, zurückgezogen) mit manuellem Retry; Push ans Fahrzeug-Tablet.
- **GSL**: Einheitenansicht: „Auftrag erhalten“, „Jetzt synchronisieren“, kopierbare Konflikte, getrennte Offline-Ablage je Zugangsart.
- **MCP**: `gsl_ressource_anlegen`, `gsl_einheit_disponieren`, `gsl_auftrag_aendern`, `gsl_auftrag_zurueckziehen`, `gsl_ressource_qr` (Status/Widerruf, nie Token/Link/PIN).
- **GSL**: Ressourcenkarte mit Gruppenkommandant (Telefon, Stellvertreter), Journal, Personal, Ausstattung sowie Verbänden und Aufteilung (Migrationen 0265–0267).
- **GSL**: Persönlicher, widerrufbarer Zugangslink für Gruppenkommandanten in den Einheitenmodus (Token `gkz_`, nur als Hash gespeichert, SMS manuell oder automatisch, Kopieren, optionale SMS-PIN, Notbremse; Migration 0266). Standardmäßig aus.
- **GSL**: Gruppenkommandant kann – wenn erlaubt – Personal und Ausstattung der eigenen Einheit offlinefähig pflegen.
- **MCP**: neun GSL-Ressourcen-Tools (`gsl_ressourcen_liste`, `gsl_ressource_details` u. a.); Zugangstoken und -links werden nie ausgegeben.
- **Straßensperren**: MCP um vorzeitiges Beenden, Teams-Warteschlange, öffentliche Freigabelinks, Kennzahlen sowie Bereichs-, Zeit- und Radiusfilter erweitert.
- **Straßensperren**: Microsoft-Teams-Webhook mit Adaptive Card, Kartenbild sowie persistenter Retry- und Duplikatschutz-Outbox ergänzt.
- **Straßensperren**: Statusseite und Infoscreen mit externen Status-/Infoscreen-Tokens, Live-Aktualisierung und widerrufbaren Zugängen ergänzt.
- **Straßensperren**: Öffentliche, widerrufbare Einzelansicht per Freigabelink (Token `rcd_`), öffentliches Feld „Grund“, Grundlage für Status-/Infoscreen-Tokens und Teams (Migration 0262); Login-Rücksprung gehärtet.
- **Straßensperren**: Navigation, kombinierbare Filter mit Merken der Auswahl, Statusansicht und Konsolen-Kachel ergänzt.
- **Straßensperren**: Zusatzdaten (Ort, Aktenzeichen, Behörde, Ausnahmen), Geometriequalität,
  Dokumentmodell und MCP-Kataloge mit Alias-Normalisierung ergänzt (Migration 0258).
- **Straßensperren**: Abschnitt wird im OSM-Straßennetz der Gemeinde ermittelt (Kreuzungen, Hausnummern,
  ganze Straße inkl. Abzweigungen) und mit Qualität bewertet; eindeutige Abschnitte werden sofort bei der
  Umfahrung berücksichtigt, andere über „Geometrie bestätigen“ bzw. die MCP-Tools
  `strassensperre_geometrie_ermitteln`/`strassensperre_geometrie_bestaetigen`. MCP-Leserechte jetzt auch für
  Objektverwalter.
- **Straßensperren**: Adressvorschläge (Photon) und Live-Prüfung von Straße/Von/Bis gegen OSM im Formular wie
  bei der Einsatzanlage; MCP-Antworten enthalten `adressvalidierung`.
- **Straßensperren**: behördliche Verordnungen als PDF anhängen (UI und MCP, auch für Partnerwehren lesbar);
  `strassensperre_entwurf_aus_pdf` liefert Textauszug und einen Felder-Entwurf (Zeitraum, Aktenzeichen,
  Behörde, Einschränkung, Ausnahmen, Straßen).
- **Straßensperren**: Verlängerungen, Änderungen und neue Verordnungen werden beim Anlegen erkannt (gleicher
  Abschnitt, anschließender Zeitraum bis 14 Tage, gleiches Aktenzeichen). Statt einer Dublette wird die
  Aktualisierung mit fertigen Feldern angeboten oder die alte Sperre nachvollziehbar ersetzt
  (MCP `possible_update`, `ersetzt_road_closure_id`, `als_neu_bestaetigt`).

- Neu: System-Admins können eingehende Mails bestimmter Adressen (z. B. info@) an andere Adressen weiterleiten (inline oder als EML, mit Retry, Schleifenschutz und Protokoll ohne Inhalt).

## 2026.10.06 (2026-10-06)

### Alarmierung zuverlässiger

- Einsätze aus DIBOS werden schneller angelegt und nicht mehr still ohne Alarmierung verloren. Push, SMS und Teams laufen über eine absturzsichere Alarm-Warteschlange mit automatischen Wiederholungen.
- Ist beim Alarm kein SMS-Gateway verbunden, wird die Einsatzinfo-SMS bis zu 60 Minuten lang nachgesendet. Wird ein Einsatz kurz nach der Anlage verknüpft und war noch nicht alarmiert, wird die Alarmierung nachgeholt.
- Gründe, warum ein Kanal nicht alarmiert hat (z. B. Übung), stehen jetzt im Einsatzprotokoll. F30 wird grundsätzlich nicht alarmiert.
- DIBOS dient als Rückfallebene für LIS (Fahrzeuge, Objektzuordnung, automatisches Schließen); die LIS-Synchronisierung wurde anhand echter Einsatzdaten verbessert.

### Android-App und Widgets

- Das Einsatz-Widget zeigt Karte, Objekt, Meldungstext, Großschadenslagen und Übungen und wird auch für normal angemeldete Handys ohne Gerätekopplung befüllt.
- Standortberechtigung wird vorab angefragt statt erst im Einsatz. In der Geräteübersicht ist die installierte App-Version sichtbar.
- Objekte und Kontakte stehen offline zuverlässiger bereit; der Synchronisierungsfortschritt wird laufend angezeigt.

### KI-Anbindung (MCP-Server)

- Einsatzcockpit lässt sich über einen OAuth-gesicherten MCP-Server mit KI-Assistenten verbinden: Objekte und Kontakte als Entwurf pflegen, Objektunterlagen übergeben, Fahrtenbuch auswerten. Freigaben bleiben immer beim Menschen in der Oberfläche.

### Einsatz-Feed für externe Systeme

- Neue, rein lesende Schnittstelle `/api/v1/feed` mit eigenen API-Key-Berechtigungen, optionaler IP-Freigabeliste und ohne Personendaten.

### Weitere Verbesserungen

- Einsatzboard: Personen verschwinden beim Verschieben nicht mehr, gerettete Personen bleiben als Karte sichtbar, Fahrzeuge von Partnerorganisationen führen nicht mehr zu Fehlern (auch in Archiv und PDF).
- Login: Links auf Einsatzseiten führen nach der Anmeldung zum ursprünglichen Ziel.
- Fahrtenbuch: Führungsrolle über das Personen-Menü wählbar; das Gerätefahrzeug wird bei neuen Fahrten vorausgewählt.
- Externe Objektpflege: Notizfeld, vollständigere Lesedaten und verbesserte mobile Darstellung.

### Hinweise zum Update

- Datenbank-Migrationen 0249–0256: `alembic upgrade head` ausführen.
- Die Anwendung läuft jetzt mit einem einzelnen Worker (`-w 1`, siehe `deploy/einsatzleiter.service` bzw. Docker), damit SMS-Gateway-Verbindungen und Hintergrundaufgaben konsistent bleiben.

Die ausführlichen Einzeleinträge dieser Version stehen in der Tabelle „Ältere Versionen“.

## Wichtige Update-Hinweise

> **Achtung beim Update auf 2026.10.06:** Die Datenbank-Migrationen 0249–0256 müssen mit
> `alembic upgrade head` eingespielt werden. Die Anwendung läuft jetzt mit genau einem Worker
> (`-w 1`, siehe `deploy/einsatzleiter.service` bzw. Docker), weil SMS-Gateway-Verbindungen und
> Hintergrundaufgaben pro Worker gehalten werden.

> **Achtung beim Update auf 2026.09.06 oder neuer:** Übungseinsätze lösen ab sofort
> serverseitig **keine** produktiven externen Aktionen mehr aus. Betroffen sind Web-Push/FCM,
> der Alarmton der PWA, die Alarmansicht des Infoscreens, Nachbar-Einladungen, das
> LIS-Statusrückschreiben an die Leitstelle und der WordPress-Bericht. Wer einzelne dieser
> Kanäle bei Übungen weiter nutzen will, muss sie in den Organisationseinstellungen unter
> „Übungseinsätze: externe Aktionen“ bewusst wieder freigeben. Realeinsätze sind nicht
> betroffen und verhalten sich unverändert.

## Ältere Versionen

| Version | Datum | Highlights |
|---------|-------|------------|
| **2026.10.06** | 2026-10-07 | **Straßensperren & Anfahrtsrouting**: Verwaltung mit Karte, Status und Infoscreen, automatische Anfahrtsprüfung je Einsatz mit Umfahrung, Einsatzinfo-/Alarm-Infoscreen-Warnung, Nachbarwehren und MCP-Tools; Routing ist Opt-in. |
| **2026.10.06** | 2026-10-06 | **Android-Einsatz-Widget**: `/api/v1/device/duty-state` liefert den laufenden Einsatz und die aktive Großschadenslage auch für per Sitzung angemeldete Handys ohne Geräte-Token; Tracking-Flags bleiben fahrzeuggebunden |
| **2026.10.06** | 2026-10-05 | **Alarmierung**: absturzsichere Alarm-Outbox (Migration 0256) mit Retry für Push und Teams, SMS-Lease gegen Doppelversand und Leader-Lock für Hintergrundschleifen, Betrieb mit einem Worker; DIBOS-Alarmierung geht nicht mehr still verloren, Nachalarmierung kurz nach Anlage verknüpfter Einsätze, Skip-Gründe im Einsatzprotokoll; Einsatzinfo-SMS bei fehlendem Gateway bis 60 min nachgesendet (Migration 0255); F30 wird nie alarmiert |
| **2026.10.06** | 2026-10-05 | **DIBOS/LIS**: schnellere Einsatzanlage mit weniger Poll-Last, DIBOS als Fallback für LIS mit Fahrzeugen, Objekt-Matching und automatischem Schließen (Migration 0254), LIS-Sync-Verbesserungen aus Trace-Analyse; **Einsatzboard**: gerettete Personen bleiben als Karte in ihrer Spalte, Personen verschwinden beim Verschieben nicht mehr, Fahrzeuge von Partner-Orgs führen in Board, Archiv und PDF nicht mehr zu Fehlern; **Fahrtenbuch**: Führungsrolle über das Personen-Flyout |
| **2026.10.06** | 2026-10-04 | **Login**: Deep-Links auf Einsatzboard und Einsatzinformation behalten ihr Ziel — nicht angemeldete Nutzer werden mit Rücksprung-Ziel (`next`) zum Login geführt, und die SSO-Anmeldung reicht das Ziel weiter |
| **2026.10.06** | 2026-10-04 | **Einsatz-Feed**: gesicherte, rein lesende Pull-Schnittstelle `/api/v1/feed` für externe Systeme mit API-Key-Scopes `einsatz:read`, `einsatz:read:kraefte` und `einsatz:read:board`, optionaler IP-Allowlist pro Key, Org-Scoping inklusive Kollaboration, strikten Whitelist-Feldern ohne Personendaten, Rate-Limit je Key sowie `ETag`/`304` über die neue Einsatz-Revision `feed_rev` (Migration 0253) |
| **2026.10.06** | 2026-09-30 | **MCP-Server**: OAuth-gesicherte KI-Anbindung mit Public-PKCE, Live-Rechteprüfung, Objektentwürfen und Arbeitskopien mit Zuordnungs-IDs, Objekt-Stammdaten für Informationen, Anfahrtsweg, Revision und Wohnanlagen, zentraler Kontaktpflege einschließlich Dublettenprüfung, Versionsschutz, Archivieren und Zusammenführen mit Bestätigung, fertig analysierten Objekt-PDFs mit zweistufigem Upload per curl (Upload-Token, `POST /api/mcp/uploads`, kein Base64 nötig, Client-Klassifizierung bleibt maßgeblich), Arbeitskopie-ID und klarere Dubletten-Meldung bei `objekt_aktualisieren`, lesenden Fahrtenbuch-Auswertungen, Profil-Widerruf und Dokumentation für Betrieb, Anwender und Entwicklung; SMS- und Mail-Freigaben je Objektkontakt, SMS-Versand sowie Kontakt-Import und -Export bleiben im UI |
| **2026.10.06** | 2026-09-26 | **Android-App**: Einsatz-Widget-Daten (Karte, Meldung, verknüpftes Objekt, GSL-Warteschlange, Übungen) in `duty-state`, App-Version der Geräte in der Geräte-Login-Übersicht (Migration 0250), Gerätefahrzeug hat Vorrang bei der Fahrt-Erfassung, Standortberechtigung wird proaktiv angefragt, Objekt-Sync meldet Fortschritt während des Downloads; Übungseinsätze wecken den Android-Live-Status still |
| **2026.10.06** | 2026-09-23 | **Externe Objektpflege**: Notizfeld und fehlende Lesedaten im Portal (Migration 0249), Akkordeon-Randfarbe nach Bestätigungsstatus; **Kontakte**: Anzeigename-Vorschlag „Nachname Vorname“, BMA-Datenblatt-Import mit geteilter Person in zwei Rollen korrigiert |
| **2026.09.19** | 2026-09-19 | **Objektpflege und Offline**: versionierte Objektunterlagen mit Dokumentviewer und geschütztem Gastzugang für externe Ansprechpartner; Objekt- und Kontakt-Detailseiten in der Android-App offline verfügbar |
| **2026.09.19** | 2026-09-16 | **Kontaktverwaltung**: Kontaktimport und -export auf ein flaches CSV-/XLSX-Format mit bis zu drei Telefonnummern direkt je Kontaktzeile umgestellt |
| **2026.09.19** | 2026-09-16 | **Wiki**: Cloudflare-Turnstile-Setup für das Kontaktformular dokumentiert |
| **2026.09.19** | 2026-09-15 | **Karten**: OSM-Kacheln über einen Same-Origin-Proxy ausgeliefert; **Kontaktverwaltung**: österreichische Mobilnummern werden automatisch als SMS-fähig erkannt, BMA-Kontakte ohne Telefonnummer sichtbar gewarnt und der Bearbeiten-Dialog sicher escaped; Objekt-Kontakt-Zuordnung überarbeitet, Android-Sync-Endpoint für Geräte- und Sitzungsanmeldung ergänzt; **Fahrtenbuch**: km-Stand bei Korrekturen gegen den Fahrtzeitpunkt geprüft |
| **2026.09.19** | 2026-09-14 | **Kontaktverwaltung**: Profilbilder und Bild-Upload ergänzt, Dubletten sowie leere Werte bereinigt, Bearbeiten und Dublettenanlage korrigiert und BMA-Import angebunden; **Fahrtenbuch**: Administration für Fahrtenbuch-Administratoren in der Navigation freigeschaltet |
| **2026.09.19** | 2026-09-13 | **Kontaktverwaltung**: zentralen Kontakt-Cutover mit Modulfreigabe, Navigation, Importübersicht und Objektverknüpfungen abgeschlossen; **Karten**: OSM-Kacheln richtlinienkonform eingebunden |
| **2026.09.19** | 2026-09-12 | **Kontaktverwaltung**: zentrales Kontaktmodul um Objektkontakt-Migration, Dublettenprüfung und Zusammenführen, Telefon-/SMS-Aktionen, BMA-Abgleich sowie XLSX-/CSV-Export und abgesicherte Import-Pipeline einschließlich Offline-Sync-Vertrag erweitert |
| **2026.09.19** | 2026-09-11 | **Kontaktverwaltung**: Fundament für ein zentrales, mandantenfähiges Kontaktmodul mit Rollen- und Modulfreigabe geschaffen; **Verwaltung**: Qualifikations-Button und Vorauswahl bestehender Qualifikationen korrigiert |
| **2026.09.19** | 2026-09-10 | **Einsatzboard**: mobile Scroll- und Pull-to-Refresh-Bedienung, kontextabhängige Aktionen, kompakte Pill-Buttons und repariertes Personen-Drag-and-Drop; **Druck/SMS**: Druckervorgaben als Fallback und SMS-Gateway-Recovery ohne falsche Entwarnung; **Fahrtenbuch**: Korrekturworkflow an die Fahrt-Erfassung angeglichen |
| **2026.09.19** | 2026-09-09 | **Einsatzboard**: Live-Updates und Drag-and-Drop für Personen, Fahrzeuge und Karten nachgebessert, damit Zuordnungen, Status und Header ohne Reload konsistent bleiben; mobile und Tablet-Darstellung weiter verdichtet |
| **2026.09.19** | 2026-09-08 | **Einsatzboard-Redesign**: Header, Lane-Header sowie Aufgaben-, Meldungs-, Personen- und Fahrzeugkarten vereinfacht, Erledigt-Gruppen und Sidebar überarbeitet, Mobile-/Tablet-Menüs ergänzt und Inline-CSS/-JavaScript ausgelagert |
| **2026.09.19** | 2026-09-07 | **Live-Updates im Einsatz- und GSL-Board**: Einheiten, Personen, Aufträge, Meldungen, Fahrzeugstatus, Ampel-Pillen, Fahrzeug-Checkliste sowie Dispatch-Zähler aktualisieren sich ohne Reload |
| **2026.09.06** | 2026-09-06 | **Probenplanung**: vollständiger Jahresplan für Übungen und Veranstaltungen mit versionierten Checklisten, Vorbereitung, Teilnehmerappell, Nachbereitung, Druck, geschützter Dokumentablage und optionalem öffentlichem ICS-Kalender; sichere Probe-zu-Übungseinsatz-Brücke mit bewusster Daten-/Medienübernahme, Doppelanlage-Schutz, Statussync und Teilnehmerübernahme; Exercise-Guard unterdrückt externe Aktionen für Übungen standardmäßig kanalweise; responsive Detail- und Jahresansicht sowie konsolidierte Navigation und Verwaltung; optionale Zusatzabhängigkeiten blockieren den App-Start nicht mehr; **Backup**: Datenbank-Dumps wieder zuverlässig wiederherstellbar |
| **2026.09.02** | 2026-09-02 | **Maschinisten-Matrix**: Jahresauswertung Mitglied × Fahrzeug (Übung/Einsatz) in der Fahrtenbuch-Statistik, als XLSX und als einseitiger farbiger A3-Querformat-Druck (lokal und über den Stationsdrucker); Maschinisten-Stufen M1–M4 im Qualifikationskatalog, pflegbar auch durch Fahrtenbuch-Administratoren; Fahrzeuge zusammenführen jetzt jederzeit über die Ressourcen-Verwaltung statt nur bei erkannten Dubletten; **Uptime-Kuma-SMS-Gateway**: synchroner `POST /api/v1/sms/send` mit Scope-Authentifizierung, Limits, Versandstatus, SMS-Protokoll und Audit |
| **2026.09.01** | 2026-09-01 | **Nachrichten-API**: persistenter externer SMS- und E-Mail-Versand mit API-Key-Scopes, Idempotenz, Empfängerstatus, Limits, Mail-Retry, SMS-Protokoll und Audit |
| **2026.08.27.1** | 2026-08-27 | **Druckregeln und Verleihschein-Autodruck**: Druckregeln als eigenes Gateway-Tab; Verleihschein-Autodruck vollständig über Druckregeln, bestehender Schalter migriert und entfernt; Übungsfilter greift nun auch bei Verleihscheinen |
| **2026.08.27** | 2026-08-27 | **Mehrfach-Login und Drucksteuerung**: mehrere Konten gleichzeitig angemeldet halten und ohne erneute Anmeldung wechseln; QR-Codes auf Einsatz- und GSL-Drucken mit optionalem PIN-Schutz je Lage; Übungs-/Echt-Filter je Druckregel |
| **2026.08.26.1** | 2026-08-26 | **Telefonbezogene Einsatzinfo-Freigabe**: SMS-Freigabe je einzelner Rufnummer, responsive Kontaktkarten, sichere Übernahme unveränderter Freigaben beim BMA-Import und Idempotenz je Empfänger |
| **2026.08.26** | 2026-08-26 | **Einsatzinfo an Objektkontakte**: je Objekt konfigurierbarer automatischer Einzelversand per E-Mail/SMS bei bestätigter Einsatzverknüpfung, mit Übungs-/Stichwortfilter, Vorlagen, kanalweiser Kontakt-Auswahl, Versandprotokoll und manuellem Retry im Board |
| **2026.08.17** | 2026-08-17 | **Umstellung auf CalVer**: Das Versionsschema wechselt von SemVer auf CalVer im Format `YYYY.MM.DD`; bei mehreren Releases am selben Tag wird ein fortlaufender Suffix (`.1`, `.2`, …) angehängt |
| **3.9.0** | 2026-08-17 | **Wetter-Modul-Härtung**: Alarm-Engine mischt lokale Stationswerte nicht mehr fälschlich mit externen Quellen (Skalar-Fallback-Bug bei 0,0-Messwerten behoben), Stationsfrische wird berücksichtigt, Kachelmann wird auch im Alarm-Pfad korrekt genutzt, Provider-Ausfall führt nicht mehr zur stillen Entwarnung, Alarm-Meldungen zeigen die tatsächliche Datenquelle statt einer fixen ZAMG-Zuschreibung; **zweistufige Wetter-Historie**: stündliche Verdichtung (`weather_reading_hourly`, Rohdaten 30 Tage / Stundenwerte 2 Jahre) mit domänenspezifischer Regen-/Windrichtungs-Aggregation statt reinem Löschen; **Wetter-Darstellung**: neue interaktive Temperatur-/Wind-Verlaufsgrafik (Chart.js, 24 h/7 Tage/30 Tage) auf `/wetter`, Quellen-Badge je Messwert, Frost-/Glatteisrisiko-Hinweis auch im Einsatz-Board-Panel, Infoscreen-Alarm-Wetterbadge nutzt jetzt denselben zentralen Wetterdienst statt einer separaten Open-Meteo-Anbindung |
| **3.6.0** | 2026-07-20 | **Förderstrecken-Planer**: Karte auf bildschirmfüllendes Vollbild-Overlay umschaltbar (alle Zeichen-/Routing-Werkzeuge bleiben bedienbar); mit einem Einsatz verknüpfte Strecke zeigt den Einsatzort als Marker (auch nach Schließen des Einsatzes); Route + Pumpenstandorte erscheinen zusätzlich als eigener, ein-/ausblendbarer Kartenlayer **🚰 Förderstrecke** in der Lageführung (inkl. WYSIWYG-Kartendruck) |
| **3.5.0** | 2026-07-18 | **Nachschlagewerke-Modul** (`/nachschlagewerke`): offlinefähige Gefahrgut-Suche nach UN-Nummer/Stoffname über den vollständigen ADR-Bestand (~2.350 Stoffe, BAM dl-de/by-2.0; täglicher Sync inkl. ZIP-Auto-Entpacken, TAB-/`;`-Formaterkennung), ERI-Karte + Deep-Links (ERICard/BAM), Rettungsdatenblätter (on-demand + Cache), Karten-Overlays Evakuierungsradius (ERG 2020) und windbezogene Ausbreitung (Kegel + Gaußsches Fahnenmodell), Offline via Service-Worker; **Datenbank-Backup & Disaster-Recovery**: automatisierte `mariadb-dump`-Sicherung beider DBs + Medien mit Retention (`app.cli backup`, systemd-Timer), **wöchentlich getestete Restore-Probe** in eine Wegwerf-DB, **Off-Site-Upload** (SFTP/SCP/rsync/FTP/FTPS/rclone, Key-Auth), dokumentiertes DR-Runbook mit RPO/RTO; **Org-Datensicherung (Self-Service)**: jede Organisation sichert ihre eigenen tenant-gescopten Daten als Download oder zeitgesteuerten Push an ein eigenes Ziel (SFTP/SCP/rsync/FTP/FTPS/rclone) und kann sie (Sysadmin) in eine neue Org zurückspielen (ID-Remapping); Lizenz auf **LGPL-2.1** vereinheitlicht |
| **3.4.0** | 2026-07-10 | **Mail-Versand je Organisation**: eigener SMTP-Server und/oder Office 365 / Microsoft Graph (App-only, Client-Credentials), automatische Fallback-Kette (O365 → eigener SMTP → globaler SMTP), Secrets Fernet-verschlüsselt, `/admin/mail`; Vorbereitung für künftigen Posteingangs-Abruf (IMAP/Mail.Read); Einsatz-Infoscreen: **Gesamtstatus im Alarm-Kopf** (rot blinkend „Status nicht gesetzt" → „Übernommen" → „Am Einsatzort"); LIS-Diagnose: automatischer Rohdaten-Capture-Start (120 min) bei neuem Einsatz, Opt-in je Org |
| **3.3.0** | 2026-07-06 | Dokument-**Volltextsuche** (PDF-Textlayer + Tesseract-OCR) in Objektverwaltung und Einsatzinfo; zentrale **Katalog-/Auswahllisten- und Karten-Symbol-Verwaltung** (Symbolbild-Upload); Gefahren mit **weiterführenden Links** (Katalog + objektspezifisch) und **UN-Nummer-Anreicherung** aus der offenen BAM-Gefahrgut-DB (dl-de/by-2.0) + Deep-Links (ERICard/BAM); neue Board-Spalte **„Objektgefahren"** (Meldungen je Gefahr automatisch beim Match, inkl. Links); **Infoscreen-Ausbau**: frei rotierende URLs je Monitor (Matrix), vererbtes Wetter, Großschadenslage-Sonderansicht, Einsatz sichtbar solange aktiv, dauerhaft kopierbare (Fernet-verschlüsselte) Monitor-URLs; **RSVP** (Zu-/Absagen) in Einsatzinfo und Infoscreen |
| **3.2.0** | 2026-07-05 | Objektverwaltung (PR 1–9): Objekte mit BMA/FSD-Block, strukturierten Gefahren, Kontakten, Merkmalen, Wohnanlagen-Daten und Stiegen-Adressen; PDF-Pipeline mit Seiten-Zerlegung (pypdf + pdf2image/Poppler), Galerie mit Bulk-Klassifikation und Fullscreen-Viewer; Objekt-Lagekarte mit Symbol-Editor; Alarm-Matching (BMA-Nr. → Adresse → Geo) mit Board-Panel und mobiler Einsatzansicht; Alarm-Infoscreen für Wandmonitore (Token, WebSocket, Idle-Modi); Objektblatt-Druck mit QR und Einsatzdruck-Anhang; KI-Dokumentklassifizierung (Vision, Review-Queue, opt-in); Offline-Precaching in der Android-App; neue Rolle „Objektverwalter"; zweistufiger Feature-Flag (System + Org) |
| **3.1.0** | 2026-07-04 | LIS/IPR-Anbindung an das Leitstellensystem der Landeswarnzentrale (SOAP/WCF): automatischer Einsatz-/Übungseinsatzabgleich, Fahrzeugstatus/-position, Meldungen, Zu-/Absagen, Dokumente, automatisches Schließen bei Abschluss in LIS, Leitstellen-Nr. als führende Kennung (Board, Archiv, Verlauf, PDF), Anrufer/Melder-Anzeige mit Klick-zum-Anrufen, Diagnose-Aufzeichnungstool; SMS-Empfang & Weiterleitung (Teams/Gruppen/Mitglieder/Ad-hoc) über native Android-Gateway-App |
| **3.0.0** | 2026-07-01 | Rebrand zu Einsatzcockpit (einsatzcockpit.com); Teilnehmerlisten-Modul (Termine/Übungen/Mannschaft, Excel-Import, PDF/Druck mit Status, Entschuldigt-Checkbox); Mannschaft als eigene Seite; SMS-Einsatzinfo je Alarm-Stichwort + manueller Gruppenversand; automatische Wetterwarnungen per Mail/Teams; Security- & Stability-Hardening (14 PRs: Tenant-Backstop, XSS-Sanitizing, FERNET_KEY-Pflicht, Device-Session-Revoke, WS-Resync, PWA-Tile-Cache, Mobile-Performance); Board: Abschnittsleiter, Karten-Journal, Meldungs-Zuweisung, Sprachdiktat bei Auftrag/Meldung; lokal gehostete Fonts statt Google Fonts; zahlreiche Timezone-, Mobile- und CI-Fixes |
| **2.9.0** | 2026-06-24 | Digitales Fahrtenbuch: Fahrterfassung mit km/BH-Zähler, Seilwinde (BH, Züge, Wartung), Maschinist-Autocomplete, Token/QR-Zugang ohne Login, Doppelfahrt-Erkennung, Schadensmeldung (Mail + Teams-Webhook), Korrektur-/Storno-Workflow; Admin-Bereich (Fahrzeuge, Zwecke, Zielorte, Einstellungen) |
| **2.8.0** | 2026-06-23 | Lokale Wetterstation (Davis Vantage Pro 2 Plus / Meteobridge PRO RED): HTTPS-Push-Ingest mit `wxst_`-Token-Auth, Rate-Limiting 120/min, denormalisierter Ist-Stand-Snapshot (Haupt-DB), separate Zeitreihen-DB `einsatzleiter_weather` (kein Bloat), Online/Offline-Indikator (15-min-Schwelle), 24-h-Sparkline Temp/Wind (lazy HTMX), Echtzeit-Szenario-Analyse aus lokalen Messwerten (Sturm/Waldbrand/Glatteis), nächtliche Retention (03:30, tägl.); Token-Verwaltung in Org-Einstellungen |
| **2.7.0** | 2026-06-22 | Geräteverleih-Modul (Artikel, Stücklisten, Barcode-Scan, SMS-Erinnerungen, Foto, Druckschein); Mobil-Navigation GSL (Burger-Menü, Bottom-Tab-Bar); UAS: echter Medien-Upload (Bild/Video), Abschluss-Banner, Flugbuch-Sperre; Drohneneinsatz direkt vom Einsatz-Board startbar; SSO: login_hint, Security-Fixes; Admin-Sidebar Mobiloptimierung |
| **2.6.0** | 2026-06-20 | UAS/Drohnen-Modul vollständig (PR 0–8): Geräteregister, Wartungsbuch, Pilotenregister (Lizenzen/Qualifikationen), UAS-Einsatz (Status-Workflow, Mindestbesetzung), Flugbuch mit Vor-/Nachflug-Checklisten (4-Augen), Notfall-/Unfall-Workflow (ACG-Meldung, Meldekette), Karte (GeoJSON, Landebefehl-Banner), PDF-Anhänge 8.1–8.6, DSGVO-Medien-Workflow, Compliance-Dashboard; SSO Microsoft Entra ID (PR 1–6): PKCE/OIDC, JIT-Provisioning, Gruppen→Rollen-Mapping, enforce_sso, Fernet-verschlüsseltes Secret |
| **2.5.0** | 2026-06-19 | GSL-Ressourcenverwaltung (Einheiten anlegen/disponieren, Mehrfach-Disposition, Fremdorganisations-Ressourcen, Ressourcen-Journal); Taktische Lagekarte nach ÖBFV E-27 (ÖNORM-Symbole, Magnetfarben, Legende); Lagekarte-Druck A4/A3 mit Druckvorschau, Fußzeile & Print-Center; SKKM-Lagemeldungs-Regelkreis (Lage→Auftrag→Kontrolle); übergreifende Meldungen mit Status-Workflow, Medien & Druck; Einsatzkarte mit Live-Updates & Foto-Upload; Kachelmann-Wetter-Primärquelle; Bürgermeldungs-Foto-Übertragung; Testsystem-Modus |
| **2.4.0** | 2026-06-13 | Wetterdaten-Integration: Nowcast (15-min), Ist-Werte, +6/+12/+24h-Vorhersage, Unwetterwarnungen (GeoSphere Austria CC BY 4.0); Sturm- und Waldbrand-Szenario-Alerts; Radar-Layer (RainViewer) auf Lagekarte; Wetter-Panel in GSL-Board und Einzeleinsatz; globale `/wetter`-Seite; org-spezifisches Opt-out |
| **2.3.0** | 2026-06-13 | Großschadenslage-Karte: Abschnitte live ohne Reload, Pin-Modus mit Reverse Geocoding, Geoman-Toolbar auf Deutsch; Stab: BMI SKKM-Einsatzjournal als erstes Tab; Dashboard: Abschnitt-Polygone auf Mini-Karte |
| **2.2.0** | 2026-06-11 | Multi-Tenancy vollständig (12 PRs): Row-Level-Isolation, Org-Onboarding, KI je Org, Speicher-Quotas, Einladungsmodell, Auto-Schließen, Rate-Limiting, API-Härtung, System-Konsole, Migration-Runbook |
| **2.0.0** | 2026-05-23 | Media-Upload + Galerie, System-Admin-Rolle, Zeitzone je Org, ZIP-Update, Python 3.14 |
