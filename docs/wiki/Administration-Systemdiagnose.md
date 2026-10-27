# Systemdiagnose

Das Systemdiagnose-Cockpit bündelt den technischen Zustand des gesamten
Einsatzcockpits. Es verwendet dieselben Dienste, Prüfungen und Reparaturaktionen
wie der [System-Admin-MCP](Administration-System-Admin-MCP). Die Seite arbeitet
organisationsübergreifend und ist auch ohne eigene Organisation nutzbar. Sie zeigt
technische Zustände, Zähler und begrenzte IDs, keine Einsatzinhalte oder Personendaten.

Der Einstieg und die sichere Einschaltreihenfolge stehen im
[System-Admin-Überblick](Administration-System-Admin-Ueberblick).

## Aufruf und Berechtigungen

**Administration → Einstellungen → Systemdiagnose**, direkt unter
`/admin/systemdiagnose`. Der Eintrag gehört zum bestehenden Verwaltungsmenü;
er eröffnet kein zusätzliches Fachmodul. Auf kleinen Bildschirmen steht er im
mobilen Verwaltungsmenü. Ergebnisse erscheinen als Karten.

Nur aktive persönliche Konten mit Rolle `system_admin` erhalten Zugriff. Normale
Admins, `org_admin`, Geräte- und QR-Sitzungen sind ausgeschlossen. Die Webseite
benötigt weder `admin_mcp_enabled` noch die persönliche MCP-Freigabe
`admin_mcp_access`. Wenn `admin_2fa_enabled` aktiv ist, greift automatisch das
zentrale MFA-Gate auch für nachgeladene Bereiche. Fehlende MFA führt zur
Verifikation; abgelaufene Sitzungen erfordern eine Anmeldung.

## Bereiche des Cockpits

| Bereich | Inhalt und Verwendung |
| --- | --- |
| Gesamtzustand | Ampel und Status der Teilkomponenten, Version, Deploy-Commit, Build-Zeitpunkt und GitHub-Zugangsstatus |
| Worker / Leader | Aktive und abgelaufene Worker, Anzahl Leader, fehlerhafte oder überfällige Loops; Bestandteil der Gesamtzustandskarten |
| Redis | Konfigurations- und Verbindungszustand des angesprochenen Workers |
| MariaDB | Verbindungsprüfung, Pool-Zähler, angewandte und erwartete Migrationen |
| Gateway / Alarmierung | Presence-Zähler, offene und überfällige Jobs für SMS und Druck, gespeicherte W&T-Dienstzustände; keine Nachrichten oder Empfänger |
| Diagnoselauf | Start einer Einzelprüfung oder Gesamtdiagnose, Kostenliste, Fortschritt und übersprungene Checks |
| Integritätsbefunde | Filter nach Modul, Schweregrad und Status, weitere Ergebnisse per Cursor, Detailansicht |
| Fehlerhistorie | Die letzten Läufe mit Status, Quelle, Zeitpunkt und Ergebnisfortschritt, in Seiten zu 25 Einträgen |
| Reparaturen | Eigene Vorschläge, Scope-Eingabe, Dry-Run, Freigabebedarf, kontrollierte Ausführung und Verifikation |
| GitHub-CI / Code | Deploy-SHA gegenüber `main`, Workflow-Läufe und PR-Zuordnung, Token-Status und lokale technische Codekorrelation |
| Audit | Letzte Admin-MCP-, Diagnose-, Reparatur-, Token- und MFA-Aktionen, in Seiten zu 25 Einträgen; ohne Payload, IP oder Benutzerangaben |

Die Ampel beschreibt technische Indikatoren. Fehlende Daten sind keine
Entwarnung. Dienstmonitorwerte sind gespeicherte Zustände. Redis und lokale
Codeanalyse beziehen sich auf den angesprochenen Worker; Worker-Heartbeats
ergänzen die Sicht auf andere Prozesse. Ein Leader je Host ist zulässig.

Zeitpunkte werden in der Anzeige über die Zeitzonenfilter formatiert, mit der
Org-Zeitzone des angemeldeten Kontos und Europe/Vienna als Fallback. Die
**Planungsuhrzeit** der Tagesdiagnose gilt hingegen immer in der Systemzeitzone
Europe/Vienna und hängt nicht von einer Organisation ab.

## Befundsprache und Details

| Schweregrad | Bedeutung |
| --- | --- |
| `info` | Technische Information ohne unmittelbaren Handlungsbedarf |
| `warn` | Auffälligkeit oder unvollständige Datenbasis prüfen |
| `fail` | Fehlgeschlagene technische Prüfung oder deutlicher Integritätsbefund |
| `kritisch` | Höchste Dringlichkeit innerhalb des Diagnosekatalogs |

`offen` bedeutet zuletzt beobachtet. `behoben` wird erst gesetzt, wenn eine
vollständige erfolgreiche Prüfung denselben Befund nicht mehr findet.
`unterdrueckt` bleibt erhalten; das Cockpit bietet keine Unterdrückungsaktion.
Ein übersprungener oder unvollständiger Check behebt keine alten Befunde.

Die Detailansicht enthält Evidenz, betroffene IDs, ausdrücklich als Hypothese
gekennzeichnete mögliche Ursache, Empfehlung sowie erstes und letztes Auftreten,
Behebungszeit und Beobachtungszähler. Hypothesen sind keine bestätigten Ursachen.
Titel und Freitexte werden auf technisches Vokabular begrenzt. Freie Evidenztexte
und Feldnamen werden maskiert; die Ausgabe erhält Zahlen und IDs in ihrer
Reihenfolge. Dadurch können auch manipulierte gespeicherte Daten keine
Personennamen über diese Ansichten ausgeben. Zum Verständnis die Check-ID und
die Beschreibung des registrierten Checks heranziehen.

## Diagnose starten und Fortschritt verfolgen

1. **Diagnoselauf** öffnen.
2. `schnell` für leichte Checks oder `voll` für den gesamten Katalog wählen.
3. Optional eine konkrete Einzelprüfung wählen; diese Auswahl hat Vorrang.
4. **Diagnose starten** auslösen. Die Seite zeigt den Lauf sofort an.

Der Hintergrundrunner arbeitet unabhängig vom Browser. HTMX fragt laufende
Aufträge alle zwei Sekunden ab und aktualisiert nur den Diagnosebereich.
Kein manuelles Neuladen ist nötig. Systemweit darf genau ein Lauf aktiv sein;
je Benutzer sind maximal sechs Starts in einer rollierenden Stunde zulässig.
Web und MCP teilen sich dieses Kontingent. Die DB schützt den globalen Slot
auch bei mehreren Hosts.

Einzelne Checks haben Zeit- und Zeilenbudgets; das Gesamtbudget beträgt
120 Sekunden. Während aktiver Einsätze oder Großschadenslagen laufen nur leichte
Checks. Mittlere und schwere Prüfungen werden mit einem Hinweis übersprungen.
`done` bedeutet daher nicht, dass jeder Check erfolgreich oder vollständig war.
Bei fehlendem Fortschritt seit mehr als zehn Minuten gibt der Runner den Slot
als fehlgeschlagen frei. Nach Einsatzende einen neuen vollständigen Lauf starten.

## Reparaturen und Freigaben

Diagnose selbst verändert keine Fachdaten. Reparaturen sind ein eigener,
standardmäßig ausgeschalteter Ablauf. Voraussetzung sind **aktive Admin-2FA und
`admin_repair_enabled`**. Die Weboberfläche benötigt keine MCP-Freigabe; ein
MCP-Aufruf benötigt weiterhin seine eigenen globalen und persönlichen Rechte.

Aus einem passenden Befund lassen sich die registrierten Aktionen vorschlagen:
festhängende Outbox-Claims freigeben oder einen Suchrückstand erneut anstoßen.
Nicht jeder Befund hat eine automatische Reparatur. Über **Registrierte Aktion
direkt simulieren** ist auch die vorhandene temporäre Upload-Bereinigung erreichbar.
Der Katalog und die zugehörigen Scope-Schemata werden aus der Reparatur-Registry
gelesen. Org-ID und Objekt-IDs im Scope ausdrücklich kontrollieren.

1. Vorschlag öffnen und begrenzten Scope als JSON eingeben.
2. **Dry-Run** ausführen. Die Simulation schreibt keine Fachdaten.
3. Vorschau und Risiko prüfen. Eine Simulation gilt maximal 15 Minuten.
4. MFA unter `/mfa/verify` frisch bestätigen (maximal fünf Minuten).
5. Bei ausführbaren Aktionen die ausdrückliche Bestätigung setzen und ausführen.
6. Ergebnis anschließend verifizieren; gegebenenfalls erneut diagnostizieren.

| Risiko | Freigabe |
| --- | --- |
| LOW | Bestätigung; nur explizit konfigurierte LOW-Aktions-IDs dürfen automatisch freigegeben werden |
| MEDIUM | Ausdrückliche Bestätigung und unveränderter `simulation_hash` |
| HIGH | Zusätzlich bestehende Freigabeseite `/admin/reparatur/freigabe/{run_id}` mit neuem TOTP- oder Recovery-Code; einmaligen Token ins Cockpit übernehmen |
| CRITICAL | Ausschließlich Simulation, keine Ausführung |

Der Idempotency-Key entsteht serverseitig. DB-Mutex, erneute Live-Rechteprüfung,
Scope-Prüfung und Vergleich der Simulation verhindern parallele oder veraltete
Ausführungen. Änderungen am Bestand erfordern einen neuen Dry-Run. Nicht
einsatzsichere Aktionen bleiben während aktiver Einsätze gesperrt. Details zu
Aktionen und Grenzen: [System-Admin-MCP](Administration-System-Admin-MCP).

## Codex-Auftrag und GitHub

Bei einem Befund **Codex-Auftrag generieren** wählen. Der Auftrag erscheint als
Text mit Kopierfunktion. Er enthält maskierte technische Evidenz, Deploy-Bezug,
Hypothese, Testidee und Abnahmekriterien sowie die Vorgabe, Änderungen nur über
Branch, Tests und Pull Request vorzunehmen. Es wird weder ein Issue noch ein
Branch oder PR auf GitHub angelegt. Die Analyse nutzt die bestehenden lesenden
GitHub-Dienste; ohne Token sind Ergebnisse eingeschränkt.

Den Token über den Link **GitHub-Zugang verwalten** in den Systemeinstellungen
prüfen, setzen oder rotieren. Tokenwerte erscheinen weder im Cockpit noch im Audit.

## Tagesdiagnose konfigurieren

Unter **System-Einstellungen → Systemdiagnose** stehen folgende Werte zur Verfügung:

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Tagesdiagnose | AUS | Aktiviert Planung und konfigurierte Diagnosemeldungen |
| Uhrzeit | 03:00 | Täglich in Europe/Vienna, unabhängig von Org-Zeitzonen |
| Umfang | schnell | `schnell` oder `voll` |
| Systemmail-Empfänger | leer | Eine oder mehrere kommagetrennte Adressen; leer bedeutet kein Versand |
| Mindest-Schweregrad | fail | `warn`, `fail` oder `kritisch`, jeweils einschließlich höherer Schweregrade |
| Wiederholungsintervall | 24 Stunden | 1 bis 720 Stunden für unverändert offene Befunde |

Die Einstellungen werden gezielt per HTMX gespeichert und sind im allgemeinen
Key-Value-Editor gesperrt. Beim Deployment entstehen weder automatische Läufe
noch Benachrichtigungen, bevor diese Konfiguration ausdrücklich aktiviert wird.

`diag_schedule_loop` läuft nur im beaufsichtigten Leader-Paket. Ein eindeutiger
DB-Tages-Slot verhindert doppelte geplante Läufe auch mit mehreren Leadern auf
verschiedenen Hosts. Der geplante Auftrag verwendet `diag_run` mit
`quelle="schedule"` und denselben Runner wie manuelle Starts.

Aktive Einsätze verschieben den Start bis zu sechs Stunden. Dauert der Einsatz
weiter an, werden nur leichte Checks eingeplant. Der Scheduler prüft minütlich
und erlaubt fünf Minuten Nachholzeit am Ende des Sechs-Stunden-Fensters. Ein
bereits belegter globaler Lauf-Slot wird nicht blockierend abgewartet. Ist das
Zeitfenster nach einem längeren Ausfall vorbei, erfolgt kein unbeschränkter
Nachholsturm; am nächsten Tag wird wieder geplant.

### Meldungen, Deduplizierung und Versandfehler

Unmittelbar nach Laufende werden neue hinreichend schwere offene Befunde geprüft,
auch nach manuell oder über MCP gestarteten Läufen, wenn Tagesdiagnose und
Empfänger konfiguriert sind. Die Sammlung verwendet den stabilen `finding_key`.
Ein unveränderter Befund wird vor Ablauf des Wiederholungsintervalls nicht erneut
gemeldet. Nach bestätigter Behebung wird die Meldungssperre zurückgesetzt;
ein späteres Wiederauftreten darf sofort erneut gemeldet werden.

Es gibt maximal **eine Sammelmail je Lauf**. Inhalt: technisches Modul,
Schweregrad, maskierter technischer Titel, Anzahl und Link zur Systemdiagnose.
Keine Namen, Einsatztexte, Zugangsdaten oder Nachrichtenausschnitte werden versandt.
Die vorhandene globale Systemmail-Konfiguration übernimmt die Zustellung.
Org-spezifische Teams- und SMS-Kanäle werden hier bewusst nicht verwendet.

DB-Claims werden vor dem Versand festgeschrieben. Ein Versandfehler blockiert
weder Lauf noch spätere Diagnose; er erzeugt `diag.notification.failed` und einen
geheimnisfreien Loghinweis. Der Claim bleibt bestehen, damit Fehler keinen
Meldungssturm erzeugen. Ein erneuter Versuch für denselben Befund erfolgt erst
mit einem späteren Lauf nach dem Wiederholungsintervall. Die Sammelmail verwendet
die konfigurierte Empfängerliste, nicht einzelne Nachrichten pro Adresse.

## Fehlerbilder und Troubleshooting

| Beobachtung | Prüfung / Maßnahme |
| --- | --- |
| Zugriff verweigert | Persönliches aktives `system_admin`-Konto verwenden, Geräte-/QR-Sitzung verlassen, MFA bestätigen |
| Startlimit erreicht | Aktiven Lauf abwarten; Benutzerkontingent von sechs Starts/Stunde beachten |
| Lauf bleibt stehen | Worker-/Leader-Karten und Audit prüfen; nach zehn Minuten greift die Stale-Recovery |
| Volllauf prüft nur wenige Module | Einsatzdrosselung und übersprungene Checks kontrollieren |
| Hypothese wirkt unvollständig | Freitext wird absichtlich streng maskiert; Check-ID, Beschreibung, IDs und Zähler heranziehen |
| GitHub eingeschränkt | Tokenstatus, Ablauf, Repository und Leserechte in den Systemeinstellungen prüfen |
| Reparatur gesperrt | Aktive 2FA, Reparaturschalter, frische MFA, Scope und Einsatzsicherheit prüfen |
| Datenbestand geändert / Hash ungültig | Neu simulieren; keine alten Bestätigungen oder Freigabetokens wiederverwenden |
| Tageslauf fehlt | Schalter, Systemuhrzeit, Tages-Slot, globalen aktiven Lauf und Sechs-Stunden-Fenster prüfen |
| Keine Mail | Empfänger, Schweregrad und Wiederholungsintervall prüfen, dann globale Systemmail und Audit `diag.notification.failed` kontrollieren |
| Befund bleibt offen | Übersprungene oder unvollständige Checks dürfen ihn nicht automatisch beheben |

## Speicherung und Betrieb

Migration 0329 ergänzt die globalen Tabellen `diag_schedule_slot` und
`diag_notification`. Sie enthalten nur Tages-Slot/Laufbezug beziehungsweise
Finding-Key, letzten Claim-Zeitpunkt und Meldezähler. Sie werden wie die übrigen
Diagnosetabellen vom Org-Export ausgeschlossen. Backup und Migrationen müssen
vor der Aktivierung erledigt sein. Diagnoseaufbewahrung und genaue Checkbudgets
stehen auf der [MCP-Detailseite](Administration-System-Admin-MCP).
