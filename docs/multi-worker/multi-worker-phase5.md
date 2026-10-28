# Phase 5: Hintergrundverarbeitung und Betriebsdiagnose

Stand: 26.10.2026. Basis ist der Phase-4-Branch mit den Phasen 2–4. Die produktive
Systemd-/Gunicorn-Konfiguration bleibt unverändert. Zusätzlich wurden die vier
bei der Nachprüfung gefundenen Fehler aus Phase 3/4 korrigiert.

## Leader und Failover

`LEADER_LOCK_PATH` bleibt die gemeinsame Sicherheitsgrenze für die Single-Leader-
Loops. Alle Worker desselben Gunicorn-Dienstes müssen denselben absoluten Pfad
beziehungsweise denselben Arbeitsordner und dieselbe lokale Datei verwenden.
`flock(LOCK_EX | LOCK_NB)` wird für die gesamte Führungsdauer gehalten. DIBOS, LIS,
Alarm-Outbox und die übrigen in `_start_background_loops()` registrierten Loops
starten ausschließlich unter diesem Lock. Ein Fehler beim Öffnen oder Sperren
startet keine Loops mehr; der Worker versucht die Übernahme alle zwei Sekunden.

Der Kernel gibt den Lock beim Prozessende, auch bei `SIGKILL`, frei. Ein wartender
Worker übernimmt beim nächsten Versuch. Die Datei wird weder beim Start noch beim
Shutdown gelöscht: Ein zweiter Inode wäre eine zweite, unabhängige Sperre.
Symlinks werden beim Öffnen abgewiesen, der Descriptor ist `CLOEXEC`. Ein per `fork`
erzeugtes Kind schließt geerbte Lock-Descriptoren, ohne den Lock des Elternprozesses
zu entsperren. Ein gestoppter oder hängender Prozess verliert seine Führung nicht
allein wegen eines abgelaufenen Diagnose-Heartbeats. Damit gibt es keinen durch
Zeitablauf ausgelösten zweiten Leader neben noch arbeitendem Code.

Beim geordneten Shutdown werden Root-Loops und entkoppelte Folgearbeiten aus
DIBOS, LIS und Alarm-Outbox abgebrochen und abgewartet. `create_leader_task()` bindet
diese Folgearbeiten an den geerbten Leader-Kontext; Aufrufe aus HTTP-Requests
behalten ihr bisheriges Verhalten. Auch die Arbeiten des Default-Executors werden
vor Freigabe des Locks beendet: `Task.cancel()` beendet laufende `to_thread()`-
Arbeit nicht. Bei einem dauerhaft hängenden Thread bleibt die Sperre gehalten,
bis Gunicorn den alten Prozess beendet. Erst danach darf der Ersatz übernehmen.

Unerwartet fehlgeschlagene Root-Loops starten nach fünf Sekunden unter demselben
Lock erneut. Konfigurationsbedingt normal beendete Loops bleiben beendet.
Ein unbekannter Teilstart wird als `loop_start_failed` gemeldet; es wird keine
zweite Gruppe gestartet. Fehlerhafte Locks und tote/überfällige Loops sind in
der Dienstüberwachung sichtbar.

Der Lock ist für den bestehenden Dienst auf **einem Host** ausgelegt. Mehrere
Hosts mit getrennten Dateisystemen oder unsicheren NFS-Locks sind damit nicht
abgesichert. Dafür wäre eine gemeinsame, gefencete Leader-Architektur erforderlich.

## Alarm-Outbox und persistente Jobs

Die SMS-/Druck-Outboxes und die adressierte Transportqueue aus den vorherigen
Phasen arbeiten weiterhin in allen Workern mit atomaren DB-Claims. Sie dürfen
auch ohne Redis aus der DB zugestellt beziehungsweise abgeglichen werden.

Die Alarm-Outbox fenced Ergebnisse zusätzlich mit `attempt_count` als Generation.
Ein alter Bearbeiter kann nach einer Übernahme keinen neueren oder terminalen
Status mehr überschreiben. Bei abgelaufenen `sending`-Claims für Push, Teams und
Mail entsteht `uncertain`: Eine externe Wirkung lässt sich nach einem Absturz
nicht ausschließen. Diese Claims werden nicht automatisch erneut vergeben.
Die bisherigen expliziten Retry-Regeln für regulär zurückgemeldete Fehler bleiben
erhalten. Ein abgelaufener SMS-Koordinator kann den bestehenden persistenten
SMS-Jobstatus wieder abgleichen; stabile logische Job-IDs verhindern eine neue
physische SMS allein durch den Koordinator-Neustart.

## Redis

Der Bus bestätigt seine Verbindung erst nach einem erfolgreichen Ping des normalen
Clients und den Subscribe-ACKs aller aktuell registrierten Kanäle. Nach einem
Abbruch werden Browser-, Gateway-, Yjs-Dokument- und Awareness-Kanäle erneut
abonniert. Erneutes Verbinden verwendet Backoff von 0,1 bis fünf Sekunden.

Der Subscriber fragt regelmäßig Nachrichten ab und sendet alle fünf Sekunden
einen Ping auf seiner eigenen Verbindung. Bleibt dessen Pong weitere fünf Sekunden
aus, beginnt die Wiederverbindung. Damit wird auch eine stille Netzunterbrechung
erkannt. Fehler beim Schließen eines alten Clients dürfen den Supervisor nicht
beenden. Handlerfehler werden isoliert; nur bekannte Kanalnamen und feste
Fehlercodes werden protokolliert, keine Exceptiontexte oder Nutzdaten.

`publish()` liefert `False`, wenn Redis fehlt, ein Transportfehler auftritt oder
kein Subscriber den Kanal abonniert hat. Ein `True` bestätigt nur die Annahme
des Hinweises durch Redis, niemals SMS-Versand, Gateway-ACK oder Papierausgabe.
Die DB-Outboxes/Claims bleiben maßgeblich. Lokaler Send und eigenes Redis-Echo
können keinen zweiten Send desselben Transportauftrags erzeugen.

Publishing- und Subscriberfehler, Wiederverbindungen sowie letzte erfolgreiche
Verbindungs-/Publish-Zeitpunkte erscheinen im Worker-Snapshot. Warnungen werden
pro Fehlercode auf höchstens eine Meldung in 30 Sekunden begrenzt; die Zähler
erfassen trotzdem jedes Ereignis.

## Gemeinsame Überwachung und Systemstatus-Seite

Migration `0315` ergänzt ausschließlich `worker_runtime`. Jeder App-/Gunicorn-
Worker schreibt alle fünf Sekunden seine PID, Prozess-ID, Host, Start-/Heartbeat-
Zeit, Ablaufzeit, Leaderrolle, Redis-Metadaten und Loop-Metadaten. Die Einträge
laufen nach 20 Sekunden ab; beim normalen Ende werden sie explizit abgemeldet.
Die Anzeige zählt frische Heartbeats, keine konfigurierten Soll-Worker und keinen
Gunicorn-Master. Nach einem Absturz können alte Einträge deshalb noch maximal
20 Sekunden sichtbar sein, auch kurzzeitig ein alter und neuer Leader-Eintrag.
Die Lock-Entscheidung verwendet diese Diagnose ausdrücklich nicht.

Alte Einträge werden nach einem Tag vom Leader stündlich in einer getrennten
Transaktion entfernt. Konkurrierende Range-Deletes in den Worker-Heartbeats
werden vermieden; ein Bereinigungsfehler rollt keinen Heartbeat zurück.

`iteration_watch()` erfasst Beginn, Erfolg und Fehler eines Durchlaufs. DIBOS,
LIS und Alarm-Outbox sowie die bereits damit instrumentierten Loops liefern
diese Zeitpunkte; sonstige Root-Loops zeigen ihren Taskstatus. Ein Erfolg
bedeutet abgeschlossener Durchlauf, nicht die erfolgreiche Außenwirkung jedes
einzelnen Auftrags. Bereits vorhandene DIBOS-/Dienstproben bleiben zuständig
für fachliche Erreichbarkeitsprüfungen. Laufende Iterationen gelten nach mehr
als `max(60 Sekunden, 3 × Intervall)` als überfällig. Ein Fehler setzt den letzten
Erfolg nicht hoch. Die letzte bekannte erfolgreiche Iteration bleibt auch nach
Ausfall ihres Workers bis zur Diagnose-Aufbewahrungsfrist sichtbar.

Die bestehende Systemstatus-Seite, ihr 30-Sekunden-HTMX-Refresh und die geschützten
Uptime-Endpunkte werden weiterverwendet. Ergänzt werden:

- aktive Worker, Leader und letzte erfolgreiche Durchläufe;
- Redis-Status je Worker sowie Fehlerzähler;
- gültig verbundene SMS-/Linux-/Windows-Gateways und letzte Heartbeats/Presence-Fristen;
- offene und überfällige SMS-/Druckjobs, überfällige Auftragsbestätigungen und `uncertain`;
- aufklappbare Worker-/Loop-Details und auf kleinen Bildschirmen scrollbar bleibende Tabellen.

Neue Dienstschlüssel sind `worker_runtime`, `redis_bus`, `sms_jobs`, `print_jobs`.
`/health/dienste` enthält zusätzlich `runtime`; bestehende Felder bleiben bestehen.
Ein fehlender Leader oder eine vollständig ausgefallene konfigurierte Redis-
Verbindung ist ein Ausfall, teilweise Verbindungen oder unklare Jobs eine
Teilstörung. Die vorhandene Karenz-/Benachrichtigungslogik bleibt erhalten.
Clients müssen die zusätzlichen Dienstschlüssel und den möglichen HTTP-Status
207/503 berücksichtigen.

Offen bedeutet nicht terminal. SMS-ACKs sind überfällig, wenn ein `dispatched`-
Job ohne Empfangsbestätigung seine Lease überschreitet. Beim Linux-Gateway gilt
dies für vorbereitete/versandte Jobs; beim Windows-Agenten für Lease-/Fetch-Jobs
ohne Übergabebestätigung. Abgelaufene Leases und mehr als fünf Minuten wartende
Queues werden zusätzlich als überfällig gezählt. Das sind Diagnosen, keine
Erlaubnis zum automatischen Zweitversand oder Nachdruck.

Gateway- und Jobdaten werden immer nach Organisation gefiltert. Gemeinsame
Prozessmetadaten sind systemweit sichtbar; sie enthalten keine organisations-
bezogenen Auftragsdaten. API- und UI-Snapshots enthalten weder Tokens/Token-Hashes
noch Telefonnummern, SMS-Texte oder signierte Artefakt-URLs. Neue Fehlerlogs
verwenden feste Codes beziehungsweise Exception-Klassennamen.

## Korrekturen der Nachprüfung aus Phase 3/4

1. Alte, nicht zugeordnete SMS-Empfänger mit `provider=sendet` werden dauerhaft
   als unklarer Job übernommen, auch außerhalb des einstündigen Nachversandfensters.
   Sie werden nicht erneut versendet. Wartende Alt-Empfänger bleiben innerhalb
   der Frist versendbar; bestätigte terminale Jobs behalten ihr Ergebnis.
2. Windows isoliert Fehler der Spoolersuche je Recovery-Job. Der betroffene Job
   und seine `uncertain`-Statusmeldung werden atomar lokal gespeichert. Andere
   Jobs und der Cloud-Start können fortfahren. Angeforderte Cancellation wird
   weitergereicht, Persistenzfehler werden nicht als erfolgreiche Recovery kaschiert.
3. Linux unterscheidet fehlende CUPS-Statusinformation von bestätigtem `printing`.
   Nach drei erfolglosen Abfragen oder 120 Sekunden seit Beginn der Unklarheit
   wird `uncertain` gespeichert. Zähler und Beginn sind restartfest. Bis dahin
   werden keine vermeintlichen Fortschrittsmeldungen oder deren Outbox-Replays
   gesendet; bei wieder verfügbarem Status kann der bestehende CUPS-Job abschließen.
4. Linux begrenzt Download-/Retry-/Poll-Übergänge atomar auf erlaubte Zustände.
   Terminale Zustände werden nicht wiederbelebt. Abbruch entscheidet atomar
   anhand des aktuellen Submit-Intents/CUPS-Jobs zwischen `canceled` und
   `uncertain`. Das gilt für Legacy und v2 sowie erfolgreiche und fehlerhafte
   verspätete Downloads.

Linux ergänzt lokal nur `status_failures` und `status_unknown_since`; die
SQLite-Migration erfolgt additiv beim Öffnen. Windows benötigt keine neue
DB-Struktur. Die Cloud benötigt für die vier Korrekturen keine weitere
Geschäftsdatenmigration. Beide Gateway-Binaries müssen die Korrekturen enthalten.
Die zugehörigen lokalen Client-Commits sind `b34b578` (Linux) und `965c5cd`
(Windows), jeweils auf dem bestehenden Phase-4-Feature-Branch.

## Prüfung und verbleibende Abnahme

Die automatisierten Nachweise umfassen echten Gunicorn mit zwei und vier Workern,
`SIGKILL` des Leaders, automatische Ersatzprozesse, exklusiv beobachtete Folgearbeit,
Lock-Vererbung, verzögerte Thread-Arbeit beim Shutdown, gleichzeitige Alarm-Claims,
alte Generationen, Redis-Abbruch einschließlich stiller Unterbrechung und erneutem
Subscribe aller Kanäle. Dedicated Redis-Neustarts prüfen Linux, Windows und den
Alarm-SMS-Protokollpfad; MariaDB prüft konkurrierende Heartbeats, SMS-, Druck- und
Routing-Claims. Migrationen wurden auf leerer MariaDB bis `0315` und zurück auf
`0314` sowie erneut nach `0315` geprüft. UI-Snapshots und Logs werden auf sensible
Nutzdaten und Mandantengrenzen geprüft; Desktop-/Mobilansicht werden gerendert.

Abschlussprüfung: **670 Cloud-Tests bestanden**, ohne übersprungene Fälle im
ausgewählten SMS-/Druck-/Gateway-/DIBOS-/LIS-/Monitoring-Regressionsumfang.
Darin enthalten sind die zwei echten Gunicorn-Konfigurationen und alle acht
Redis-/Alarm-Protokollfälle einschließlich tatsächlicher Container-Neustarts.
Zusätzlich bestehen **sechs MariaDB-Integrationstests**, **55 Linux-Tests** und
**95 Windows-Tests** einschließlich der vier HTML-/HTTP-Browserfälle. Letztere
verwenden auf dem Linux-Prüfhost einen temporären lokalen Chromium-Wrapper;
produktive Windows-Browserargumente wurden nicht verändert. Der Windows-Cross-
Build und die Typ-/Lint-Prüfungen der betroffenen Cloud-Komponenten bestehen.
Beim .NET-Lauf gab es eine NuGet-Cachewarnung des Prüfhosts; sie betraf den
schreibgeschützten Vulnerability-Cache, nicht Compiler- oder Testfehler.
Desktop und Mobilansicht wurden mit Chromium gerendert; die neue Ansicht
verursacht bei 390 Pixeln Breite keinen horizontalen Seitenüberlauf.
Die ursprünglichen Fehler-Reproduktionen der Nachprüfung wurden ebenfalls erneut
ausgeführt: SMS-Migration und beide Linux-Fälle bestehen; die Windows-Recovery-
Reproduktion kehrt regulär zurück. Ein zusätzlicher Alarm-Recovery-Lauf besteht
mit 29 Tests, einschließlich Schutz vor verspäteten Ergebnissen nach Ablauf.

Opt-in-Prozesstest:

```sh
RUN_WORKER_PROCESS_TESTS=1 python -m pytest -q tests/test_background_gunicorn.py
```

Die Probe verwendet echte ECP-Lifespans und ersetzt ausschließlich die fachlichen
Leader-Poller durch eine lokale Exklusivitätsprobe. Heartbeat-/Übernahmeintervalle
sind im Test verkürzt. Die SQLite-Probe ersetzt keine MariaDB-Transaktionsprüfung
und keine physische Geräteabnahme.

Für Redis-Neustarttests ausschließlich einen eigenen, lokal gebundenen Container
mit festem Host-Port verwenden. Dynamisch zugewiesene Docker-Host-Ports können
sich beim Stop/Start ändern und sind deshalb für diesen Nachweis ungeeignet.
Die Gatewaytests erwarten den Namenspräfix `ecp-phase2-test-` und die Variablen
`ECP_REDIS_TEST_URL`/`ECP_REDIS_TEST_CONTAINER`; die Alarm-SMS-Varianten verwenden
`REDIS_RESTART_TEST_URL`/`REDIS_RESTART_TEST_CONTAINER`. MariaDB-Tests verwenden
`MARIADB_TEST_URL` und ausschließlich eine dafür angelegte Testdatenbank.

**Geräteabnahme bleibt offen:** Echte Alarm-SMS einschließlich Empfängerzustellung
und realem Provider-Fallback sowie physische Windows-/Linux-Ausdrucke und zwei
Windows-PCs am selben Drucker müssen weiterhin unter zwei/vier Workern geprüft
werden. Die behobenen Codefehler und simulierten Spooler ersetzen diese Abnahme
nicht. Es erfolgt keine produktive Freigabe oder Worker-Erhöhung.

## Rollout, Kompatibilität und Rollback

Die Änderungen liegen auf Feature-Branches und wurden nicht in `main` integriert
oder produktiv ausgerollt. Vor einer späteren Bereitstellung vollständige DB- und
Gateway-Journal-Sicherungen erstellen, alle alten Worker kontrolliert beenden,
Migration `0315` ausführen und erst danach die neue Cloud starten. Alte und neue
Leader-Implementierungen nicht gleichzeitig betreiben: Der alte Fail-open-Pfad
kann die neue Sicherheitsgrenze umgehen. Alle Worker brauchen denselben Lockpfad
und passende Schreibrechte. Die Service-Datei und ihre Workerzahl wurden nicht verändert.

Ein Code-Rollback kann die additive Diagnosetabelle zunächst stehen lassen. Für
ein DB-Downgrade zuerst alle neuen Worker stoppen und danach auf `0314` zurückgehen;
nur die Worker-Diagnose geht verloren, keine SMS-/Druck-/Presence-Daten. Bereits
als `uncertain` übernommene Alt-SMS bleiben terminal. Nie mögliche Außenwirkungen
auf `queued`/`pending` zurücksetzen und keine lokalen Journale löschen. Alte
Gateway-Versionen enthalten die dokumentierten Fehler erneut; deren Rollback
erfordert deshalb Drain beziehungsweise manuelle Prüfung unklarer Ausgaben.
