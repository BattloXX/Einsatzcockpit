# Windows-Druckagent: Offline-Status und fehlendes Tray-Symbol

Stand 23.10.2026. Die folgenden Ursachen sind am Code und in Regressionstests bestaetigt.
Es besteht kein direkter Zugriff auf KDOF1 oder dessen produktive Windows-/Cloud-Logs;
eine konkrete Historie dieses Rechners wird deshalb nicht als beobachtet behauptet.

## Bestaetigte Ursachen mit Codefundstellen

1. **Protokollvertrag der Konfigurationsnachricht:**
   Cloud `app/services/print_agent_service.py:agent_config_sync` gibt `agent_id` als Zahl aus.
   Im veroeffentlichten Agent v2026.10.23 erwartet
   `src/Einsatzcockpit.PrintAgent.Core/Protocol/Messages.cs:39` dagegen `string AgentId`.
   `System.Text.Json` konvertiert diese Zahl nicht automatisch in einen String.
   Auch Job-IDs sind auf der Cloud numerisch, im Agent aber lokale Strings.
   Der Empfang von config_sync kann daher abbrechen; spaetere Lease-/Intent-/Statusnachrichten
   haben denselben Vertragsfehler. Der neue Vertragstest verwendet echte numerische JSON-Felder.
2. **Empfangsfehler wurden verdeckt:**
   Der alte `CloudConnection.RunAsync` setzt connected direkt nach dem Handshake, wartet
   im Heartbeat-Timer und faengt Fehler mit leerem catch ab. Ein fehlgeschlagener Receiver
   kann dadurch bis zum naechsten Timer-Tick unerkannt bleiben. Dienststatus, kurzzeitig
   moegliche Inventaruebertragung und dauerhaft fehlende Verbindung widersprechen sich nicht.
   Zusaetzlich kann der Runtime-Start Inventar senden, bevor Connect fertig ist. Diese
   Start-Race wurde entfernt; das Inventar wird bei jedem Connect nach Hello gesendet.
3. **Disconnect-Race ueber Workergrenzen:**
   Cloud `app/routers/ws.py:_mark_agent_offline` prueft bisher nur die lokale
   `connected_print_agent_ids`-Registry. Ein alter Socket auf Worker A kann den DB-Status
   offline setzen, obwohl Worker B bereits eine neue Verbindung angenommen hat.
   Anschliessend wird sogar bedingungslos ein Offline-Broadcast gesendet.
   `gateway_online_id` hatte bereits einen DB-Heartbeat-Fallback; die Ursache war also
   nicht ausschliesslich eine lokale Registry-Abfrage in der HTTP-Ansicht.
4. **Tray-Start verlangt Schreibrechte auf Dienstordner:**
   Im veroeffentlichten v2026.10.23 ruft `src/Einsatzcockpit.PrintAgent/Program.cs:13`
   `paths.EnsureDirectories()` auf, bevor die CLI den Befehl tray auswertet.
   `AgentPaths.EnsureDirectories` versucht auch spool/logs/requests/updates anzulegen.
   Ein normaler Benutzer darf in den geschuetzten ProgramData-Dienstordner nicht schreiben;
   der Prozess kann bereits vor Erstellung des NotifyIcon beenden. Der neue Tray-Einstieg
   liest Status und Logs, ohne die Dienstverzeichnisse anzulegen.

Historische Agent-Fundstellen:
- https://github.com/BattloXX/Einsatzcockpit-Windows-Gateway/blob/v2026.10.23/src/Einsatzcockpit.PrintAgent.Core/Protocol/Messages.cs#L39
- https://github.com/BattloXX/Einsatzcockpit-Windows-Gateway/blob/v2026.10.23/src/Einsatzcockpit.PrintAgent/Program.cs#L13
- https://github.com/BattloXX/Einsatzcockpit-Windows-Gateway/blob/v2026.10.23/src/Einsatzcockpit.PrintAgent.Core/Cloud/CloudConnection.cs

## Korrektur

- Migration 0306 fuegt vier nullable Presence-Spalten hinzu, ohne Token oder Auftraege zu aendern.
- Authentifizierte Verbindungen beanspruchen eine Connection-ID. Hello und Heartbeat verlaengern
  ihre Gueltigkeit um 90 Sekunden. Inventar/Readiness sind getrennte, historische Daten.
- Heartbeat, Disconnect und Ablauf-Sweep verwenden atomare, nach Org/Gateway/Connection-ID
  eingeschraenkte UPDATEs. Ein alter Disconnect darf weder eine neue Verbindung beenden noch
  einen falschen Offline-Broadcast ausloesen. Der Sweep prueft die Ablaufzeit auch im UPDATE.
- Detailseite, Liste und Lease-Kandidaten verwenden die gemeinsame DB-Presence. Die lokale
  Registry dient nur der Socket-Zustellung. Redis bleibt fuer verteilte Nachrichten erhalten;
  die Presence selbst funktioniert unabhaengig von Redis. Ohne Bus bleibt das Lease-Polling.
- Nach Worker-Absturz wird eine unerreichbare Verbindung spaetestens nach 90 Sekunden als
  offline berechnet, auch wenn der Sweep erst spaeter den gespeicherten Status bereinigt.
- Tokenrotation/Widerruf und neues Pairing loeschen die alte Presence sofort. Sockets pruefen
  Token und Eigentum vor jeder Nachricht und bei Leerlauf mindestens alle 30 Sekunden.
- Numerische Agent-/Job-IDs werden nur auf ID-Properties konvertiert; Tokens, Spool-Referenzen
  und andere Strings bleiben unveraendert. Lokale SQLite-Schluessel bleiben Strings.
- Erst ein gueltiges config_sync bestaetigt den lokalen Online-Status. Empfang und Heartbeat
  werden gemeinsam ueberwacht, Verbindungs-/Sende-/Empfangs-/Schliessoperationen sind begrenzt.
- Strukturierte Logs enthalten je nach bekanntem Stand Gateway-ID, Connection-ID, Zeitpunkt,
  Ereignis und HTTP-/Close-Code. Rohe Ausnahmeantworten, Header, URLs, Codes und Tokens
  werden in den neuen Verbindungslogs nicht ausgegeben. Ein HTTP-403 wird als abgelehnter
  Handshake gemeldet, nicht spekulativ als ungueltiges Device-Token.
- Diagnose: Online/Offline/Nicht gekoppelt, befristete Live-Verbindung, letzter Heartbeat mit
  Alter, letzte Verbindung, Inventar, Version und Druckbereitschaft. Alle 15 Sekunden per
  HTMX sowie manuell aktualisierbar; Rolle, Mandantengrenze, no-store/no-referrer bleiben erhalten.
- LocalSystem, DPAPI, Dienstautostart und das Lease-/Intent-/Recovery-Protokoll bleiben erhalten.

## Tests und Grenzen

Erfolgreich lokal:
- 192 ausgewaehlte Cloud-Tests: Presence, Migration, Gateway-UI, WebSocket, Lease/Redundanz,
  Legacy-Dispatch, Druckregeln, Public-Tenant-Isolation und Routenkonventionen.
- 68 .NET-Tests, darunter numerischer Cloud-Vertrag, regelmaessige Heartbeats, Receiver-Fehler,
  Reconnect und Neustart der Connection-Lifecycle.
- 20 Pester-Installer-Regressionen.
- Solution-Build einschliesslich Windows-Anwendung: 0 Warnungen, 0 Fehler.
- Ruff, gezieltes Mypy fuer Presence/Router und git diff --check erfolgreich.
- Migration upgrade/downgrade/upgrade mit erhaltenem Gateway und Token-Hash erfolgreich.

Der Worker-B-Test startet einen **separaten Python-Prozess**, der keine lokale Socket-Registry
von Worker A besitzt. Ein weiterer HTTP-Test prueft die Diagnose mit leerer lokaler Registry.
Das alte-Disconnect-Rennen wird mit einer neuen Verbindungszuordnung eines anderen Workers
und anschliessendem realen Ende des alten Test-WebSockets nachgestellt; Status und Broadcasts
werden geprueft. Zwei gleichzeitig verbundene Agents und Tokenrotation/-widerruf sind abgedeckt.
Tests fuer Prozess-/Dienstneustart pruefen den Verbindungs-Lifecycle; ein echter Windows-Neustart
und physischer Testdruck werden dadurch nicht als bestanden behauptet.

Windows-CI ergaenzt `tests/Tray.Smoke.ps1`: Die echte veroeffentlichbare EXE muss als Tray
bei explizit verweigertem Schreibzugriff auf ihren Dienstordner weiterlaufen, ohne dort Dateien
anzulegen. Dieser Test ist unter Linux nur syntaktisch pruefbar; sein Windows-CI-Ergebnis
wird separat dokumentiert. Er prueft Prozessstart/Rechte, kein sichtbares Symbol auf KDOF1.

Ein bestehender Legacy-Dispatch-Test setzte zufaellig Gateway-/Printer-ID 1 voraus und lief
in kombinierter Ausfuehrung gegen ein vorher angelegtes Agent-Ziel. Seine Datenanlage wurde
isoliert; die getestete Legacy-Statusuebergangspruefung bleibt unveraendert.

## Deployment und Abnahme

1. Cloud-Backup nach bestehendem Verfahren; Migration `alembic upgrade head` (0306) vor dem
   Start der neuen Worker ausfuehren. Alle Cloud-Worker neu starten, damit alte Sockets
   neu verbinden. Keine dauerhafte Mischung alter und neuer Presence-Schreiber betreiben.
2. Neuen Windows-Agent-Release bauen/paketieren. **v2026.10.23 enthaelt diesen weiteren
   Bugfix noch nicht.** Kein neues Pairing erforderlich; Token und SQLite bleiben erhalten.
3. Agent auf KDOF1 aktualisieren und Dienst starten. Im Ereignisprotokoll/Agent-Log
   authentication_succeeded, config_sync und danach Heartbeats/Online-Diagnose kontrollieren.
4. Tray als angemeldeter normaler Benutzer starten oder einmal ab-/anmelden.
   PowerShell: `& "C:\Program Files\Einsatzcockpit\PrintAgent\EinsatzcockpitPrintAgent.exe" tray`.
   LocalSystem/Session 0 ist kein Benutzerdesktop. Taskleisten-Menue fuer ausgeblendete
   Symbole pruefen. Das Dienstkonto nicht umstellen.
5. Mit wechselnden Backend-Workern, Netzunterbrechung, Dienst- und Windows-Neustart pruefen:
   Reconnect, frischer Heartbeat, korrekter Status, HP-Druckziel bereit und physischer Testdruck.
   Inventar alleine ist weiterhin kein Online-Nachweis.

Produktive KDOF1-Abnahme und ein neuer oeffentlicher Release sind damit noch nicht automatisch
als erledigt behauptet. Code und Regressionen stehen in Cloud-PR #715 und Agent-PR #5. Die GitHub-CI des Agenten
(39980130644) und der Cloud (39980067513, Container 39980067550) ist erfolgreich,
einschliesslich des echten Windows-Tray-Starttests mit verweigerten Schreibrechten.
Ein Abschlussnachtrag nutzt vorhandenes Ping/Pong nach jedem Heartbeat, um gesunde
Verbindungen auch bei pausiertem Lease-Polling zu bestaetigen; die Installationsanleitung
verweist fuer diesen weiteren Bugfix auf einen neueren Release als 2026.10.23.

## Geaenderte Dateien dieses Bugfixes

### Cloud

- `app/models/gateway.py`
- `app/routers/ui_gateway.py`
- `app/routers/ws.py`
- `app/services/gateway_service.py`
- `app/services/print_agent_loop.py`
- `app/services/print_agent_service.py`
- `app/templates/gateway/agent_detail.html`
- `app/templates/gateway/liste.html`
- `docs/print-agent-architektur.md`
- `tests/test_gateway_dispatch.py`
- `tests/test_print_agent_service.py`
- `tests/test_print_agent_ui.py`
- `tests/test_print_agent_ws.py`
- `alembic/versions/0306_print_agent_presence.py`
- `app/services/print_agent_presence.py`
- `app/templates/gateway/_agent_diagnose.html`
- `tests/test_migration_0306_print_agent_presence.py`
- `tests/test_print_agent_presence.py`

### Agent

- `.github/workflows/build.yml`
- `README.md`
- `src/Einsatzcockpit.PrintAgent.Core/Cloud/CloudConnection.cs`
- `src/Einsatzcockpit.PrintAgent.Core/Cloud/IWebSocketTransport.cs`
- `src/Einsatzcockpit.PrintAgent.Core/Protocol/Messages.cs`
- `src/Einsatzcockpit.PrintAgent.Core/Runtime/AgentRuntime.cs`
- `src/Einsatzcockpit.PrintAgent/Program.cs`
- `src/Einsatzcockpit.PrintAgent/Worker/AgentWorker.cs`
- `src/Einsatzcockpit.PrintAgent.Core/Protocol/ProtocolIdConverter.cs`
- `tests/Einsatzcockpit.PrintAgent.Tests/ProtocolConnectionTests.cs`
- `tests/Tray.Smoke.ps1`

