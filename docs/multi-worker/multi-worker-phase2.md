# Multi-Worker – Phase 2: Gateway-Presence und adressiertes Routing

Stand: 25.10.2026. Implementierungsbasis: Cloud `origin/main` `faa41045`, einschließlich
WS-Stabilisierung (#723) und Intent-Kompatibilität (#724). Die Arbeit liegt auf
`feat/multi-worker-phase2` in einem separaten Worktree; lokale Änderungen des ursprünglichen
Arbeitsbranches werden nicht verändert. Die Bestandsanalyse aus Phase 1 bleibt die
Grundlage für die weiteren Phasen.

## Verhalten

SMS-Gateways, Linux Print-&-Alarm-Gateways und Windows-Druckagents verwenden eine gemeinsame
DB-Presence-Logik in `app/services/gateway_presence.py`. Die Windows-API in
`print_agent_presence.py` bleibt als kompatibler Adapter erhalten. Die vorhandenen
`Gateway.agent_*`-Spalten werden auch für Linux verwendet; eine zweite Presence-Tabelle
für Druckgateways wird nicht eingeführt.

Jeder authentifizierte Connect erhält eine zufällige 32-stellige Connection-ID und eine
Worker-ID aus Hostname, PID und Prozesskennung. Die Workerkennung wird nach einem Fork
anhand der PID neu gebildet. Die gemeinsame Presence enthält Gateway-Typ, Gateway-ID,
Organisation, Connection-ID, Worker-ID, Verbindungszeit und Ablaufzeit. Bei SMS bezeichnet
Gateway-ID die `SmsGatewayToken.id`; Typ und Organisation gehören immer zur Adresse.
Socketobjekte und lokale Tasks bleiben ausschließlich im Socket-Worker.

Der letzte erfolgreich committete Connect desselben Tokens gewinnt. Ein älterer Socket
kann weder die Presence verlängern noch die neuere Verbindung beenden. Disconnects und
Heartbeats sind atomare, nach Organisation, Gateway und Connection-ID eingeschränkte
Updates. Ein neuer Connect ersetzt eine alte Connection auch über Workergrenzen; die alte
Verbindung erhält keine neuen Aufträge und wird spätestens durch die lokale Prüfung
geschlossen. Bei SMS werden zusätzlich die bereits vorhandenen lokalen Zombie-Sockets
beim Reconnect geschlossen.

Die Presence läuft nach **90 Sekunden ohne gültiges Lebenszeichen** ab. Windows und Linux
verlängern sie bei Hello/Heartbeat; beim SMS-Protokoll zählt eingehender Verkehr einschließlich
Ping/Pong als Lebenszeichen. Inventar und Druckerberichte ersetzen bei Druckgateways keinen
Heartbeat. Nach Ablauf kann der alte Heartbeat die Verbindung nicht wiederbeleben; ein neuer
Handshake ist erforderlich. Onlineabfragen berechnen die TTL direkt aus der DB, ohne einen
Sweep oder Disconnect nach einem Prozessabsturz vorauszusetzen. Der vorhandene Sweep räumt
zusätzlich abgelaufene SMS-, Linux- und Windows-Presence auf.

Die Presence speichert auch den beim Connect gültigen **Token-Hash als Bindung**. Ein
geänderter oder widerrufener Token macht eine alte Verbindung daher selbst dann ungültig,
wenn eine andere Schreibstelle nur den Token ändert. Regulärer Widerruf, Rotation und
Pairing löschen außerdem die bisherige Connection-Zuordnung. Prüfungen erfolgen vor der
Verarbeitung, beim Zustellen und regelmäßig auch ohne eingehende Nachrichten. Die
Hintergrundprüfung läuft spätestens alle fünf Sekunden; direkte Verarbeitung/Zustellung
prüft sofort. Bereits begonnene externe SMS-/Druckvorgänge lassen sich damit nicht rückgängig
machen.

Für administrative Diagnose liefert
`gateway_presence.describe(db, kind, org_id, gateway_id)` einen mandantengetrennten Snapshot
mit diesen Verbindungsinformationen und abgeleitetem `online`/`offline`. Fremde IDs liefern
`None`. Token-Hashes sind kein Bestandteil dieses Snapshots. Die bestehenden Gateway-
Onlineabfragen und SMS-Gerätelisten verwenden die DB-Presence. Ein Windows-Agent erfüllt
nicht mehr die Verfügbarkeitsprüfung eines Linux-Gateways.

## Routing und Rückmeldungen

`app/services/gateway_routing.py` legt ausgehende Nachrichten zunächst in
`gateway_message` ab. Jede Nachricht adressiert **Typ, Organisation, Gateway-ID,
Connection-ID und Worker-ID**. Sie wird nicht an andere Verbindungen umgeleitet.

Der Socket-Worker erhält einen Redis-Hinweis auf `ec:gw`, `kind=route`, mit Nachrichten-ID
und Zieladresse. Er liest die verbindliche Adresse und den Inhalt aus der DB. Zusätzlich
prüft jeder Worker seine Queue alle 500 ms. Dadurch funktioniert derselbe Pfad ohne Redis,
bei Redis-Startfehler und während eines Redis-Neustarts. Der lokale Sender versucht die
Zustellung sofort; Redis beschleunigt die Zustellung auf anderen Workern.

Vor jedem Send prüft der Empfänger die aktuelle Presence und übernimmt die Nachricht mit
einem atomaren Zustandswechsel `queued -> sending`. Nur ein Aufrufer kann gewinnen. Eigener
Publisher, Subscriber-Echo, wiederholte Bus-Hinweise und paralleles Polling können deshalb
keinen zweiten Send **derselben Transportnachricht** auslösen. Gateway-Prüfung und Claim
werden in MariaDB durch die Verbindungssperre gegen parallele Connects serialisiert.
Der Socket-Send ist auf fünf Sekunden begrenzt.

* Linux-Druckaufträge gehen ausschließlich an `PrintJob.gateway_id` in derselben Org.
  Ein totes Ziel führt nicht zur Zustellung an ein anderes Gateway.
* `config_sync` geht nur an das ausgewählte Linux-Gateway. Druckerbezogene Testseiten und
  jobbezogene Abbrüche werden zum zugehörigen Gateway aufgelöst. Fehlende oder fremde Ziele
  werden abgewiesen. Discovery und Updatehinweise dürfen ausdrücklich orgweit sein.
* Windows-Hinweise (`job_available`, Config und Abbruch) nutzen denselben adressierten
  Transport. Lease-/Intent-Antworten und ACKs bleiben auf dem anfragenden Socket. Die
  bestehende Lease-/Intent-/Recovery-Logik wird nicht ersetzt.
* SMS-Auswahl, Verfügbarkeitsprüfung und Bulk-Gerätelisten lesen die gemeinsame Presence.
  Prioritäten, explizite Auswahl, Einzelrotation und EUS-Fallback bleiben erhalten.
* SMS-Ergebnisse und die erste Linux-Statusantwort werden nach Org, Gateway, Connection
  und Jobschlüssel der Transportnachricht zugeordnet und in der DB gespeichert. Der
  anfragende Worker kann das Ergebnis ohne lokale Socket-Registry und ohne Redis-Rückkanal
  abholen. Linux-Statusänderungen prüfen zusätzlich Org/Zielgateway und erwarteten Zustand;
  eine späte Meldung setzt einen terminalen Job nicht zurück.

Nach dem Socket-Send steht der Transportzustand auf `delivered`; das ist kein Nachweis
für eine physisch versandte SMS oder einen Ausdruck. Bei fehlendem Geschäftsergebnis
bleibt das bisherige Timeout-Verhalten erhalten. Beim Linux-Druck bleibt der Auftrag nach
Antwort-Timeout `sent`, ohne automatische erneute Zustellung. Ein Prozessabbruch nach dem
Claim hinterlässt einen unklaren Send; **`sending` wird nicht blind erneut gesendet**.
Ein erneuter API-/Geschäftsaufruf ist eine neue Transportnachricht und benötigt weiterhin
die fachlichen Idempotenz-/Claim-Regeln aus den Folgephasen.

Hinweise sind standardmäßig 30 Sekunden gültig; wartende SMS-/Druckaufträge verwenden ihre
jeweilige Aufruffrist. Alte oder abgelaufene Zielverbindungen erhalten keine Nachrichten.
Abgelaufene `queued`-Einträge werden bei der Ergebnisabfrage als abgelaufen behandelt.
Nachrichten einschließlich Nutzdaten und Ergebnissen werden nach einem Tag automatisch
in begrenzten Batches entfernt. Die Tabelle ist eine kurze Transportqueue, kein dauerhaftes
SMS-Archiv und kein Ersatz für `PrintJob` oder die fachliche Alarm-Outbox.

## Redis und Lifecycle

`ws_bus.start()` startet einen überwachten Subscriber. Nach Verbindungsfehlern werden Client
und Subscription geschlossen und mit Backoff von 100 ms bis fünf Sekunden neu aufgebaut.
`publish()` meldet einen Fehler als `False`; ein nicht erreichbarer Bus wird nicht als
erfolgreicher Publish ausgegeben. Gateway-Zustellung hängt durch die DB-Queue nicht mehr
von einem erfolgreichen Publish ab. Browser-/CRDT-Ereignisse bleiben Pub/Sub-Ereignisse
und erhalten dadurch kein dauerhaftes Replay.

Der Routing-Loop läuft auf **jedem Worker**, unabhängig vom fachlichen Leader-Lock.
Beim Shutdown wird er beendet und die eigenen Connections werden mit ihrer Connection-ID
abgemeldet. WS-Finalizer schirmen diese begrenzten DB-Aufräumarbeiten gegen die normale
Abbruchscope ab. Ein harter Prozessabbruch bleibt über die TTL abgesichert. Ein Worker
setzt beim Start niemals die Presence anderer Worker pauschal zurück.

## Migration und Betriebsfolge

Neue Migration: `0312_gateway_presence_routing`, Vorgänger `0311` auf der verwendeten
`main`-Basis. Bestehende Migrationen 0289, 0306 und 0307 werden nicht verändert.

* `gateway`: nullable `agent_worker_id` und `agent_connection_token_hash`.
* `sms_gateway_token`: nullable `connection_id`, `worker_id`, `connected_at`,
  `presence_expires_at`, `connection_token_hash`; vorhandenes `last_heartbeat_at` bleibt.
* `gateway_message`: mandantengetrennte Transportnachrichten mit Zieladresse, Zustand,
  Ablaufzeit, optionalem Jobschlüssel und Ergebnis; Indizes für Zielworker/Fälligkeit,
  Jobzuordnung und Aufbewahrung. Die Tabelle ist im Tenant-Register aufgenommen.

Alte Token, Regeln, Historien und Windows-Leases bleiben erhalten. Es gibt absichtlich
keinen Backfill einer aktiven Presence aus historischen Heartbeats. **Migration vor dem
Start des neuen Codes ausführen und danach alle Worker/Gateway-Sockets kontrolliert neu
verbinden lassen.** Eine Mischung alter und neuer Presence-Schreiber ist nicht vorgesehen.
Die inzwischen in `main` vorhandenen Revisionen 0309–0311 bleiben unverändert.
Vor jedem Rollout einen eindeutigen Alembic-Head prüfen; doppelte Revisionsnummern oder
zwei unaufgelöste Heads dürfen nicht ausgerollt werden.

Die produktive Workerzahl, systemd-Datei, Docker-Startparameter und Gateway-Konfiguration
werden in dieser Phase nicht geändert. Die übrigen Blocker aus Phase 1 – insbesondere
fachliche SMS-Empfängerclaims, globale Gerätelimits, Providerwechsel bei unklarem Ausgang,
Inbox-Deduplizierung, Druckfolgeaktionen und Leader-Härtung – bleiben eigene Folgearbeiten.
Diese Phase allein ist daher keine allgemeine Freigabe für produktive zwei bis vier Worker.

## Tests und Reproduktion

Neue Tests prüfen die drei Gateway-Typen einschließlich Worker-/Connection-ID, abgelaufener
Presence ohne Sweep, Tokenrotation/Widerruf, mehreren Organisationen und gezielter Config-/
Kommandozustellung. Die Prozessprüfungen verwenden einen echten FastAPI-WebSocket in
Prozess A und einen frischen Python-Prozess B ohne gemeinsame Socket-Registry. Ein weiterer
Test verschiebt den echten WebSocket nach B, beendet die alte Verbindung in A und stellt
anschließend weiterhin ausschließlich an B zu.

`test_gateway_routing_redis_restart.py` prüft standardmäßig einen TCP-Redis-Protokollserver
mit Verbindungsabbruch, Ausfallfenster und anschließendem neuen Server. Zusätzlich enthält
es einen opt-in Test gegen einen echten, eigens angelegten Redis-Container samt
`docker restart`. Ein **fester Hostport** ist erforderlich, da Docker einen automatisch
vergebenen Port beim Neustart ändern kann. Beispiel mit freiem lokalen Port:

```bash
docker run -d --rm --name ecp-phase2-test-redis -p 127.0.0.1:16379:6379 redis:7-alpine
ECP_REDIS_TEST_CONTAINER=ecp-phase2-test-redis \
ECP_REDIS_TEST_URL=redis://127.0.0.1:16379/0 \
python -m pytest -q tests/test_gateway_routing_redis_restart.py
docker stop ecp-phase2-test-redis
```

Die MariaDB-Regression ist in `tests/mariadb/test_gateway_presence_routing.py` und läuft
im vorhandenen MariaDB-CI-Job mit. Vier konkurrierende Transaktionen übernehmen genau eine
Nachricht, vier parallele Connects hinterlassen genau einen aktuellen Besitzer; alte und
fremde Disconnects/Heartbeats bleiben wirkungslos. Gegen eine isolierte Testdatenbank:

```bash
DATABASE_URL='<isolierte MariaDB-Test-URL>' alembic upgrade head
MARIADB_TEST_URL='<isolierte MariaDB-Test-URL>' \
python -m pytest -q tests/mariadb/test_gateway_presence_routing.py
```

Abnahme: 418 Cloud-/Gateway-/Tenant-/Monitor-Tests im kombinierten Lauf; anschließend
19 gezielte Status-/Dispatch-/WS-Regressionen einschließlich zweier zusätzlicher
Negativfälle sowie drei MariaDB-Transaktionstests. Ein bestehender Monitor-Test verwendet
eine eigene Organisation, damit zurückgebliebene Gateway-Testdaten seine Aggregation nicht
verfälschen.

Lokal geprüft: die breite SMS-/Gateway-/Windows-/Print-/Bus-/Leader-Regression, echte
A/B-Prozesse, Redis-Neustart sowie die Migration auf einer isolierten MariaDB 12.3 von
leerer DB bis Head und `0312 -> 0311 -> 0312`. Zusätzlich Upgrade/Downgrade mit erhaltenen
Bestandsdaten auf SQLite, Ruff für Produktivcode und neue Tests, gezieltes Mypy und
`git diff --check`. Es werden weder echte
SMS versandt noch echte Drucker angesprochen. Testcontainer sind von bestehenden Diensten
getrennt und werden nach der Abnahme entfernt.

## Rollback

Vor einem Rollout DB-Backup und kompatible Code-/Agent-Versionen sichern. Bei Rücknahme
neue Übergaben stoppen, Worker kontrolliert auf einen Prozess zurückführen und die Sockets
neu verbinden lassen. Transportzustände und Windows-Leases nicht pauschal zurücksetzen:
Ein bereits begonnener Send kann eine Außenwirkung gehabt haben.

Additive Spalten können beim Code-Rollback zunächst bestehen bleiben. Die neue Queue darf
bei einem Schema-Downgrade erst nach geordnetem Drain und Abgleich unklarer Sends entfernt
werden; alle neuen Worker müssen zuvor beendet sein. Ein Downgrade nach 0311 entfernt
`gateway_message` und die neuen Bindungs-/Workerfelder, aber weder die vorhandene
Windows-Presence aus 0306 noch die Windows-Lease-Daten aus 0289. Nach Rücknahme den
Single-Worker-Smoke für alle drei Gateway-Typen durchführen.
