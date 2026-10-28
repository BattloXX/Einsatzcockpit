# Phase 6: isolierte Gesamtabnahme und Aktivierungsvorbereitung

Stand: 26.10.2026. Basis: Cloud `8b265aee` (Phase 5), ursprüngliche main-Basis
`faa41045`. Dieser Branch enthält die Phasen 2–6; er wurde nicht nach main
gemergt und nicht produktiv aktiviert. Die bestehende Systemd-Datei, `Dockerfile`
und `docker-compose.yml` bleiben unverändert.

**Keine produktive Freigabe.** Der Auftraggeber hat ausschließlich isolierte
Protokolltests freigegeben. Reale Alarm-SMS, Modem-/Provider-Fallback und physische
Windows-/Linux-Ausgaben einschließlich zweier PCs an einem Drucker bleiben offen.
Die [Rollout-Anleitung](multi-worker-rollout.md) und Dateien unter
`deploy/proposals/multi-worker/` sind überprüfbare, ausdrücklich zu aktivierende
Vorschläge.

## Testaufbau und Aussagegrenzen

`e2e/multi_worker/` startet eigene, nur an Loopback gebundene MariaDB-12.3- und
Redis-7-Container sowie echten Gunicorn mit der vorgeschlagenen Konfiguration.
Alle Migrationen werden auf einer neuen Testdatenbank bis `0315` angewendet.
Der zweite Lauf verwendet dieselbe Suite mit einem Worker. Worker, Konfiguration
und Gateway-Owner werden anhand echter Prozess-IDs und DB-Presence ermittelt.
Eine sichere Konfigurationsprüfsumme wird je Worker verglichen. Die Testdienste
und Gunicorn-Prozessgruppen werden anschließend entfernt; Produktivdienste und
andere Container werden nicht angefasst.

Die App verwendet ihren normalen Lifespan einschließlich Leader-Lock, Job-Claims,
Lease-Fristen, Routing und Alarm-Outbox. Externe LIS-/DIBOS-/Mail-/Such-Poller sind
über Konfiguration deaktiviert; ihre fachlichen Außensysteme werden nicht
angesprochen. Anders als beim Exklusivitätsprozesstest aus Phase 5 wird der
Leader-Loop hier nicht durch einen Test-Loop ersetzt.

Der ausschließlich im Test importierte ASGI-Einstieg
`e2e.multi_worker.acceptance_app:app` benötigt Opt-in, Testschlüssel und einen
Testdatenbanknamen. Er ermöglicht gezielte HTTP-Auslösung auf einem bestimmten
Worker und protokolliert nur IDs/Owner/Publisher. `app.main:app` importiert diesen
Einstieg nicht. Die produktiven WebSocket-, Download-, Lease-, Intent- und
Statusendpunkte werden unverändert benutzt. Zwei echte Chromium-Kontexte mit
verschiedenen Benutzern prüfen das Einsatzboard.

SMS-/Linux-/Windows-Gegenstellen sind Python-Protokollclients, keine ausgelieferten
Gateway-Binaries. Ihre persistierten Testjournale zählen simulierte Übergaben.
Print-Clients laden echte signierte PDFs und senden Intent-/Statusmeldungen, rufen
aber keinen Spooler auf. Windows-Polling ist im Peer auf zwei Sekunden verkürzt;
die Server-Lease von 60 Sekunden, Watchdog und Gateway-Presence von 90 Sekunden
bleiben unverändert. SMS-`sent` belegt hier ausschließlich die simulierte Annahme;
`delivery_status=unverified` bleibt erhalten. Ein verlorenes positives Ergebnis
wird `uncertain`, nicht erneut versendet.

Prüfhost: Python 3.14.4, Gunicorn 26.2.0, Uvicorn 0.54.0, SQLAlchemy 2.1.5,
redis-py 8.1.0, PyMySQL 1.2.3, Playwright 1.64.0. Tatsächliche Serverversionen,
Testergebnisse und Laufzeiten stehen im bereinigten
[Ergebnisprotokoll](phase6-results.json). Die Verwendung
dieser Versionen ist kein Nachweis für abweichende produktive Versionsstände.

## Abnahmematrix

| Auftrag | Isolierter Nachweis | Geräte-/Betriebsnachweis |
| --- | --- | --- |
| 1. Alarm-SMS über anderen Worker | Echte Alarm-Outbox, Jobstatus und Publisher/Owner-Grenze, eine simulierte Übergabe je ID | Alarm auf Testtelefon und belegbare Zustellung offen |
| 2. Gleichzeitige SMS | 12 Jobs aus vier parallelen Auslösern, eindeutige IDs und je eine Übergabe | Modem-/Provider-Last offen |
| 3. Gateway-Abbruch beim Versand | Abbruch nach Versuch, echte Lease, terminal `uncertain`, verspätetes Ergebnis wirkungslos | Abbruch am realen Modem offen |
| 4. Linux-Druck | Drei parallele Jobs, anderer Worker, PDF, Intent, `done`, je eine Übergabe | CUPS und Papier offen |
| 5. Windows-Druck | `job_available` über Workergrenze, Lease, PDF, Intent und Status | Windows-Spooler und Papier offen |
| 6. Zwei Windows-Agents | Priorität und exklusiver Claim; Übernahme vor Intent mit Generation 2; nach Intent kein automatischer Nachdruck | Zwei reale PCs am selben Drucker offen |
| 7. Worker-Neustart | `SIGKILL` des Socket-Owners während offener Windows-Übergabe, Ersatzworker, Reconnect/Statusabgleich; neuer Job erfolgreich | Reales Agent-Recovery ergänzen |
| 8. Redis-Neustart | Eigenen Redis stoppen/starten; sichtbarer Ausfall; SMS/Linux über DB; Windows durch Polling; erneute Abonnements; dreifaches Redis-Replay ohne zweite Ausgabe | Zielnetz und Monitoring-Alarmierung prüfen |
| 9. Parallele Browser | Zwei echte Benutzer und WebSockets auf unterschiedlichen Workern; parallele Meldungen und Aufgaben ohne fehlende/mehrfache Karten | Weiterer fachlicher Browserumfang offen |
| 10. WS-Reconnect | Browser offline, Socket schließen, Meldung verpassen, nach Reconnect genau einmal nachladen | Reales Netz ergänzen |
| 11. Organisationen | Fremdes Gateway-ACK, Incident-HTTP/-WS und Monitoring-Scope; Tokenwiderruf | Vollständige Rollenmatrix separat |
| 12. Hintergrund-Failover | Leader mit `SIGKILL` beenden; neuer Leader mit erfolgreichem echtem Alarm-Outbox-Durchlauf; Follower ohne Leader-Loops; Gateway-TTL abwarten | Externe LIS-/DIBOS-Seiteneffekte separat |

Der abschließende Bilanztest verlangt ausschließlich terminale Jobs und prüft
Diagnoselogs auf die zufällig erzeugten Testtokens, Cookies, signierten Parameter
und den vollständigen SMS-Testinhalt. `uncertain` zählt als nachvollziehbarer,
ungeklärter Auftrag, niemals als erfolgreiche Ausgabe. Der physische Nachweis
„keine Doppel-SMS/kein doppeltes Blatt“ wird daraus nicht abgeleitet.

## Während der Abnahme gefundene und behobene Fehler

1. Drei parallele PDF-Downloads konnten denselben technischen QR-Benutzer oder
   seine Rollenverknüpfung gleichzeitig anlegen. Der zweite INSERT lieferte
   Duplicate-Key. Savepoints und gezieltes Nachlesen des Gewinners erhalten die
   Idempotenz. Der gesamte idempotente Artefakt-Aufbau wird bei verworfenen
   Transaktionen mit MariaDB-Code 1020/1213 höchstens dreimal versucht; das betrifft
   keine physische Übergabe und setzt keinen Druckstatus zurück.
2. MariaDB 12.3 meldete bei inzwischen geänderten Datensätzen Snapshot-Konflikte,
   unter anderem beim Projektionseintrag eines SMS-Ergebnisses. Neue SMS-/Routing-
   Sessions verwenden deshalb `READ COMMITTED` über einen nur dieser Session
   zugewiesenen Engine-Ableger. Das gilt bereits vor der ersten Query und erneut
   nach Commit/Rollback; ein MariaDB-Test prüft den Rückfall der normalen Sessions
   auf ihre unveränderte Standardisolation. Claims und Kapazitätsgrenzen bleiben
   gesperrt und atomar. Eager-geladene
   Organisationsdatensätze werden beim Gateway-Lock nicht unnötig mitgesperrt.
   Die globale DB-Isolation bleibt unverändert. Hintergrund: Seit MariaDB 11.6.2
   ist `innodb_snapshot_isolation` standardmäßig aktiv; das Sperren eines nicht
   zur Lesesicht passenden Datensatzes kann die Transaktion mit 1020 verwerfen.
   Inzwischen setzt die [zentrale Snapshot-Regel](../wiki/Administration-Datenbank-Snapshot-Isolation.md)
   neue App-Sessions ab MariaDB 11.6.2 auf `innodb_snapshot_isolation=OFF`
   (`DB_SNAPSHOT_ISOLATION_OFF=true`); die bestehenden READ-COMMITTED-Ableger bleiben erhalten.
   Siehe [MariaDB-Dokumentation](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables#innodb_snapshot_isolation).
3. Im ersten vollständigen Lauf schlug eine von zwei parallelen Browser-Meldungen
   mit HTTP 500 fehl (MariaDB 1020 beim INSERT). Meldungs-/Aufgabenanlage sperrt
   nun zuerst die Zielspalte und wiederholt ausschließlich noch nicht bestätigte,
   reine DB-Transaktionen bei 1020/1213. Incident-Zugriff wird je Versuch erneut
   geprüft. Uploads, Objektseiten und Broadcasts liegen nach dem erfolgreichen
   Commit und werden durch diesen Mechanismus nicht wiederholt. Andere Fehler,
   insbesondere Verbindungsabbruch mit unbekanntem Commit-Ausgang, werden nicht
   automatisch wiederholt.
4. SQLAlchemy konnte gebundene SMS-Inhalte oder Tokens in SQL-Fehlerdiagnosen
   ausgeben. `hide_parameters=True` unterdrückt diese Parameter. Der vorgeschlagene
   Gunicorn-Accesslog lässt Querystrings und Referer weg, die signierte Tokens
   enthalten können. Das ersetzt keine Prüfung weiterer Infrastruktur-Logs.

Betroffene produktive Dateien: `app/db.py`, `app/routers/gateway_api.py`,
`app/routers/ui_incident.py`, `app/services/db_transactions.py`,
`gateway_presence.py`, `gateway_routing.py`, `incident_qr_service.py`,
`incident_service.py`, `sms_dispatch_service.py`, `sms_outbox.py`.
Hinzu kommen isolierte E2E-Clients, gezielte Regressionstests, Dokumentation und
optionale Startvorschläge. **Keine weitere DB-Migration in Phase 6.**

## Ergebnisse und offene Risiken

| Abschlussprüfung | Ergebnis |
| --- | --- |
| Gesamtsuite, zwei Worker | 13 bestanden, 0 fehlgeschlagen, 322,49 Sekunden |
| Gesamtsuite, ein Worker | 13 bestanden, 0 fehlgeschlagen, 344,80 Sekunden |
| SMS-/Druck-/Routing-/Hintergrund-Regression | 440 bestanden, 4 explizite Redis-Opt-in-Fälle übersprungen |
| Board-/Upload-Regression | 35 bestanden |
| MariaDB-Konkurrenz und Isolation über Commits | 7 bestanden |
| Gezielte Transaktions-/Konfigurations-/Redaktionstests | 32 bestanden, bereits im 440er-Umfang enthalten |
| Statische Prüfung | Ruff erfolgreich; mypy für neun betroffene Module erfolgreich |
| Optionales Compose-Override | Werte 1/2/3/4 und Stopfrist 150 Sekunden validiert, keine Bereitstellung |

Die vier übersprungenen Regressionen benötigen eigene Redis-Opt-in-Variablen
(`test_sms_alarm_multiworker.py`, `test_gateway_routing_redis_restart.py`). Die
Phase-6-Gesamtsuiten führen ihre Redis-Unterbrechungen tatsächlich durch.
Bestehende Warnungen betreffen SQLite-Fixture-FK-Zyklen beim Abbau und die
veraltete Coroutine-Erkennung einer Abhängigkeit; sie sind keine fehlgeschlagenen
Tests. Der Single-Worker-Lauf bestand noch mit der strengeren Erst-Poll-Annahme
des Konkurrenztests; die produktive Anwendung entspricht dem finalen Stand.

Beide Gesamtläufe bilanzieren jeweils 16 SMS-Jobs (15 `sent`, einer absichtlich
`uncertain`) und zehn Druckjobs (acht `done`, zwei absichtlich `uncertain`). Kein
Auftrag bleibt offen, kein geprüfter Testwert erscheint als Secret oder voller
SMS-Inhalt im Diagnoselog. Der Geräteausfall wurde nach 89,81 Sekunden bei zwei
Workern beziehungsweise 89,03 Sekunden bei einem Worker als offline ausgewertet.
Die beobachteten Ersatzworker-Zeiten einschließlich Diagnosebereinigung betragen
21,98 beziehungsweise 16,07 Sekunden. Testdienste und Prozessgruppen wurden
anschließend bereinigt.

Die ersten Läufe waren ausdrücklich nicht erfolgreich: Zunächst scheiterte der
parallele Linux-PDF-Aufbau, danach der erste Gesamtlauf mit **12 bestanden / 1
fehlgeschlagen** an der Browser-Schreibkonkurrenz. Nach der Korrektur besteht die
gezielte Wiederholung des Browser-Tests einschließlich paralleler Aufgaben und
Reconnect. Verbindliche Abschlusszahlen sind dem Ergebnisprotokoll zu entnehmen;
fehlgeschlagene Erstläufe werden dort ebenfalls ausgewiesen.

Eine weitere Wiederholung scheiterte am Konkurrenztest: Bei gleichzeitigem Poll
kann der nachrangige Agent den Queue-Datensatz kurz sperren, ohne ihn zu erhalten.
Der bevorzugte Agent darf dann wegen `SKIP LOCKED` eine leere Antwort erhalten.
Die bisherige Testannahme „der allererste Poll liefert zwingend einen Grant“ war
zu streng. Der Test pollt nun begrenzt erneut und verlangt weiterhin, dass der
nachrangige Agent keinen Grant erhält und insgesamt nur ein Agent übernimmt.
Der abgebrochene Fall hinterließ einen wartenden Testjob und führte folgerichtig
auch zu einer fehlgeschlagenen Schlussbilanz; dieser Lauf bleibt als
**11 bestanden / 2 fehlgeschlagen** dokumentiert. Die produktive Lease-Vergabe
wurde dafür nicht geändert.

- Hardware-Abnahme und produktive Freigabe bleiben offen. Die Client-Basis muss
  mindestens Android `e5f11a3`, Linux `b34b578` und Windows `965c5cd` beziehungsweise
  nachweislich deren Änderungen enthalten. Diese Feature-Commits sind keine
  Aussage über bereits integriertes main oder installierte Releases.
- Ein abgestürzter Socket-Owner kann bis zum Ablauf der 90-Sekunden-Presence als
  online erscheinen; Diagnose-Worker bis zu 20 Sekunden. Das ist zeitlich
  begrenzt, nicht sofortige Ausfallerkennung. Die gemessenen Zeiten stehen im
  Protokoll und müssen für den Alarmbetrieb akzeptiert werden. Die beobachtete
  Ersatzworker-Zeit enthält auch das Abwarten alter Diagnosezeilen; sie ist
  keine direkte Messung der Dauer des Kernel-Lockwechsels.
- Der Dateilock schützt einen Host beziehungsweise gemeinsam und korrekt lokal
  gesperrten Inode. Mehrere App-Container mit eigenen Lockdateien oder mehrere
  Hosts sind damit nicht freigegeben. Einen Lock nie im laufenden Betrieb löschen.
- Lageführungs-Presence und Bearbeitungshinweise in `app/routers/ws.py`
  (`_lft_presence`, `_lft_locks`) bleiben workerlokal. Die über Redis versandte
  Benutzerliste ist daher kein globales Verzeichnis; zwei Worker können denselben
  weichen Bearbeitungshinweis erteilen. Die HTTP-Versionsprüfung ersetzt keinen
  Nachweis atomarer konkurrierender Kartenänderungen. Diese Abnahme deckt
  Meldungen/Aufgaben ab, keine vollständige Lagekarten-/Yjs-Kollaboration bei
  Redis-Unterbrechung. Dafür ist eine eigene Abnahme erforderlich.
- Die Protokollclients prüfen kein reales EUS-/Provider-Failover und keine
  Spooler-/Modem-Recovery. Die Unit-/Gatewaytests der vorherigen Phasen bleiben
  erforderlich; ein Peer-Journal ist kein Gerätejournal.
- 3-/4-Worker-Lastläufe werden erst nach der freigegebenen 2-Worker-Produktionsstufe
  ausgeführt. Die Konfiguration ist für diese Werte vorbereitet und validiert;
  daraus folgt kein Lastnachweis. Auch DB-Poolgrößen und PDF-Ressourcen müssen am
  Zielhost unter repräsentativer Last gemessen werden.

## Reproduktion

Auf einem isolierten Prüfhost mit Docker, installierten Projekt-Testabhängigkeiten
und Chromium, vom Repository aus:

```sh
ECP_RUN_MULTIWORKER_ACCEPTANCE=1 .venv/bin/python -m pytest -q e2e/multi_worker \
  --ecp-workers 2 --ecp-artifacts /tmp/ecp-phase6-abnahme-w2
ECP_RUN_MULTIWORKER_ACCEPTANCE=1 .venv/bin/python -m pytest -q e2e/multi_worker \
  --ecp-workers 1 --ecp-artifacts /tmp/ecp-phase6-abnahme-w1
```

Optional `ECP_ACCEPTANCE_BROWSER` auf die Chromium-Datei setzen. Nur frische
Artefaktverzeichnisse verwenden. Ohne Opt-in überspringt die Suite ihre Fälle.
Ein Lauf braucht wegen echter Lease-/Presence-Fristen mehrere Minuten. Die
Artefakte enthalten ausschließlich künstliche Testdaten; rohe Logs, Testjournale
und Screenshots trotzdem geschützt halten. Versionierte Ergebnisse enthalten
keine Tokens, Telefonnummern, SMS-Inhalte oder signierten URLs.

Nach der ausdrücklich freigegebenen späteren Stufe kann derselbe Protokollumfang
mit `ECP_POST_PRODUCTION_LOAD_APPROVED=1` und `--ecp-workers 3` beziehungsweise `4`
wiederholt werden. Das allein ist noch kein repräsentativer Lasttest: Anzahl
gleichzeitiger Browser, Alarmempfänger, PDF-Jobs und DB-Verbindungen sowie
Latenz-/Ressourcengrenzen müssen vor dieser Stufe vereinbart werden.
