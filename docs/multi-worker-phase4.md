# Multi-Worker, Phase 4: Druck-Gateways

Stand: 26.10.2026. Cloud-Basis `71c2714b` (Phase 3), Windows-Basis `afbf064`
(main, einschließlich Protokollfixes aus PR #6), Linux-Basis `fdfb4a2` (main).
Client-Commits: Linux `5b1d2d5`, Windows `562c360`.
Keine Änderung an produktiver Workerzahl oder `deploy/einsatzleiter.service`.

## Verbindliche Architektur

`PrintJob` bleibt der dauerhafte Auftrag mit eindeutiger DB-ID und bestehendem
Idempotenzschlüssel. Ein manueller Nachdruck hat eine neue ID. Die DB-Presence,
90-Sekunden-TTL, Tokenbindung und Connection-ID-Prüfung aus Phase 2 gelten für
beide Gatewaytypen. Nur der zuständige Worker hält den Socket. Adressierte
`gateway_message`-Datensätze und Redis-Hinweise auf `ws_bus.CH_GW` erreichen ihn;
das DB-Polling bleibt bei verlorenen Hinweisen oder Redis-Ausfall verfügbar.
Ein eigener Publisher und sein Subscriber-Echo teilen denselben Transport-Claim.

### Linux

`linux_print_service.prepare` sperrt Gateway und Auftrag und speichert den
Zustandswechsel `queued -> sent` zusammen mit genau einem Transport. Wiederholte
Dispatcher-Aufrufe senden denselben Auftrag nicht erneut. Die Auswahl ist auf
Organisation und `PrintJob.gateway_id` begrenzt; ein anderes Gateway bekommt keinen
versehentlich fremden Auftrag. Ein nicht erreichbares Gateway lässt den Auftrag
zunächst `queued`. Ein Workerabsturz zwischen Commit und Socket-Send verliert ihn
nicht: der Socket-Worker liest den bereits gespeicherten Transport aus der DB.

Der neue, in jedem Worker laufende Linux-Loop prüft die Queue alle zwei Sekunden.
Windows-Zustellung wird bereits bei Jobanlage als `delivery=lease` gespeichert;
deshalb kann auch ein Absturz vor der nachfolgenden Dispatch-Funktion den
Windows-Auftrag nicht stranden lassen. Für bestehende Windows-Routen bleibt das
Lease-Polling der eigentliche Zustellmechanismus.

Ein ungesendeter Linux-Transport läuft nach 30 Sekunden ab. Nur wenn sein Status
noch atomar als `queued` beziehungsweise nie gesendetes `expired` nachweisbar ist,
kann er erneut eingereiht werden (höchstens fünf Push-Versuche). Ein Auftrag ohne
Gateway läuft nach 30 Minuten aus. Nach möglichem Socket-Send, nach Intent oder bei
fehlenden Transportbelegen erfolgt keine automatische erneute Ausgabe. Fehlende
Geschäftsrückmeldung läuft nach zehn Minuten auf `uncertain`; laufende
`printing`-Meldungen verlängern diese Frist. Der bisherige 30-Minuten-Watchdog
behandelt auch ältere Push-Aufträge ohne neue Deadline konservativ als unklar.

Das neue Linux-Gateway meldet `print-status-v2`. Der Auftrag enthält dann ein
zufälliges `dispatch_token`, eine Generation und `requires_intent=true`. Nach
Download/Rendering fordert das Gateway per `print_intent` die Cloud-Freigabe an.
Die Cloud prüft Presence, Token, Organisation, Ziel, Status, Generation und Frist.
Sie speichert `phase=submitting` vor ihrer positiven Antwort. Eine Wiederholung
desselben gültigen Intents ist erlaubt; ein unklarer oder abgeschlossener Auftrag
kann keine neue Freigabe erhalten. Erst danach speichert das Gateway seinen
lokalen SQLite-Intent und ruft CUPS auf. Der WebSocket-Reader bleibt währenddessen
frei, damit die RPC-Antwort verarbeitet werden kann.

Das lokale Journal verhindert eine zweite CUPS-Übergabe desselben Jobs, auch nach
Neustart, parallelem Spool-Durchlauf und erneutem Redis-Ereignis. Ein Neustart in
der Übergabelücke ohne gespeicherte CUPS-ID ergibt `uncertain`. Sobald eine
CUPS-ID vorhanden ist, wird ausschließlich dieser Auftrag abgefragt. CUPS-Abbruch
und Fehler nach der Übergabe sind unklar und lösen keinen neuen CUPS-Aufruf aus.
Download- und Konfigurationsfehler vor Intent dürfen weiterhin begrenzt wiederholt
werden. Nach Ablauf einer signierten Download-URL kann das gekoppelte Gateway eine
neue URL für denselben Auftrag anfordern; das ist kein neuer Druckauftrag.

Ein fehlendes CUPS-Backend aktiviert keinen simulierten erfolgreichen Druck mehr.
Das explizit für Tests injizierbare `FakeBackend` bleibt erhalten. Der produktive
Agent bleibt mit ausstehenden Druckaufträgen funktionsfähig, während andere
Gatewayfunktionen wie Alarmempfang weiterlaufen.

Gateway-ACKs speichern den Empfang in der DB. Geschäftsstatus wird unmittelbar im
Auftrag verarbeitet, somit auch für den auslösenden Worker sichtbar. Eine
`job_status_ack` bestätigt erst nach DB-Verarbeitung den berichteten lokalen Status.
Bis dahin bleibt er in der lokalen Outbox und wird nach Reconnect erneut gemeldet.
Ein verspätetes ACK eines älteren Status bestätigt keinen neueren lokalen Zustand.
Cloud-`uncertain` bleibt trotz späterer Gateway-Erfolgsmeldung unverändert und
benötigt die vorhandene manuelle Auflösung.

### Windows

Lease-Polling (standardmäßig 15 Sekunden), `job_available`, Prioritätsrouten,
Readiness, Generationen, Lease-Token, Renewal, Intent und Reconnect-Abgleich bleiben
erhalten. `job_available` läuft weiterhin über den adressierten Redis-/DB-Transport.
Ein verlorener Hinweis nimmt keinem Agenten die Möglichkeit zum Lease-Polling.

Die Lease-Abfrage nutzt auf MariaDB `FOR UPDATE SKIP LOCKED` und zusätzlich das
bestehende bedingte UPDATE. Bei zwei PCs für denselben Netzwerkdrucker gewinnt
höchstens einer. Die beste bereite Route darf sofort übernehmen; eine nachrangige
Route erst nach der bestehenden Grace-Frist. Eine Lease ohne Intent darf nach
Ablauf neu vergeben werden. Ab `submitting`/`spooled` ist ein fehlender Ausgang
`uncertain` und kein Anlass für die Übernahme durch einen Ersatzagenten.

Ein wiederholtes Einreihen setzt aktive oder unklare Leases nicht mehr auf
`queued` zurück. Statusänderungen vergleichen zusätzlich die gelesene Phase und
Generation. Dadurch kann ein Downloadfehler aus einem älteren Zustand keine
zwischenzeitlich erteilte Spooler-Freigabe rückgängig machen.

Ein sicherer Fehlschlag nach Intent benötigt jetzt ausdrücklich
`not_submitted=true`, Phase `submitting` und eine fehlende Spoolerreferenz. Das neue
Windows-Gateway setzt diesen Nachweis nur bei einer nachweislichen Vorab-Ablehnung.
Ein abgebrochener bereits gespoolter Job, ein Kommando mit Fehler-Exitcode und ein
unklares IPP-Ergebnis oder eine fehlende IPP-Job-ID ergeben `uncertain`. Sie dürfen
keinen automatischen Ersatzdruck auslösen. Fehlende Queue, ungültiger Druckmodus
und andere echte Vorab-Ablehnungen bleiben sichere Fehler. Vor Intent kann der
bestehende Retry-/Ersatzagentenpfad weiterarbeiten.

Lokaler Endstatus und Ergebnis-Outbox werden nun in derselben SQLite-Transaktion
committet. Die Outbox wird bis zum Cloud-ACK wiederholt; auch alte Meldungen werden
von der Cloud bestätigt, ohne einen Endzustand zu ändern. Lokale abgeschlossene
IDs bleiben nach der PDF-Bereinigung als Dedup-Tombstones erhalten. Die
Spoolerabfrage verwechselt einen Enumerationsfehler nicht mehr mit einer leeren
Warteschlange; fehlerhafte Referenzen gelten nicht als erfolgreicher Druck.
Das entspricht dem Fehlervertrag von
[Microsofts EnumJobs-Dokumentation](https://learn.microsoft.com/en-us/windows/win32/printdocs/enumjobs).

## Kompatibilität und Auflösung

Alte Linux-Gateways verstehen weiterhin `print_job` und können positive Status
melden. Ohne v2 fehlen ihnen Cloud-Intent und belastbare Nichtübergabe-Nachweise;
ihr negativer oder unklarer Ausgang wird deshalb konservativ `uncertain`. Ihre
bisherigen lokalen CUPS-Retry-Lücken kann ein reines Cloud-Update nicht schließen.
Vor produktiver Freigabe müssen die betroffenen Linux-Gateways aktualisiert sein.
Das neue Gateway arbeitet auch an einer alten Cloud mit dem alten Einphasenprotokoll;
der verbesserte Cloud-Abgleich benötigt die neue Cloud. Ohne ausgehandelte
Status-ACK-Fähigkeit bleibt die alte Transportbestätigung kompatibel erhalten.

Alte Windows-Agenten behalten ihre Lease-/Intent-Kompatibilität. Fehlt bei einer
Meldung nach Intent der neue Nichtübergabe-Nachweis, wird kein sicherer Fehlschlag
unterstellt. Ein Update ist für die präzise sichere Vorab-Ablehnung erforderlich.
`done` beschreibt den beobachteten Spooler-/Gatewayabschluss und ist keine Garantie,
dass der Bediener das Papier tatsächlich vorgefunden hat.

Die vorhandenen Aktionen „als gedruckt markieren“, „verwerfen“ und „gezielt
nachdrucken“ bleiben erhalten. Auflösung und optionaler Nachdruck werden gemeinsam
committet. Ein bedingtes UPDATE und ein stabiler Request-Key verhindern einen zweiten
Nachdruck durch Doppelklick oder konkurrierende Worker. Für den Nachdruck entsteht
bewusst eine neue ID; der unklare ursprüngliche Auftrag wird nicht wieder geöffnet.

## Dateien und Migration

Migration `0314` ergänzt `gateway.print_protocol` (Standard 1) und
`print_job.push_route_id`. Token, vorhandene Jobs, Windows-Lease-/Intent-Spalten und
lokale Windows-DBs bleiben bestehen. Linux verwendet die bestehenden Phase-,
Generation-, Token- und Deadline-Spalten mit `delivery=push`; Windows bleibt bei
`delivery=lease`.

Cloud: `linux_print_service.py`, `print_dispatcher.py`, `print_agent_service.py`,
`print_watchdog.py`, Presence/Routing, `ws.py`, Startup/Shutdown, manuelle Auflösung
und Druckmodelle. Linux: `spool.py`, `print_manager.py`, `cloud_connector.py`,
`main.py`. Windows: `JobProcessor`, `JobStore`, `RecoveryService`, Protokoll und
Spooler-/Druckweg-Abfragen. Dazu kommen Regressionen und Migrationstests.

## Teststrategie und Abnahme

Ergebnis der Abschlussprüfung: **303 Cloud-Tests bestanden**; zwei opt-in Redis-Fälle
werden im Standardlauf übersprungen. Separat bestehen **vier Redis-Neustarttests**
(einschließlich echtem Container-Neustart für beide Gatewaytypen) und **fünf MariaDB-
Integrationstests**. Die Clients bestehen mit **49 Linux-Tests** und **88 Windows-Tests**;
ein Windows-Browsertest bleibt ohne `ECPA_TEST_BROWSER` ausgenommen. Der vollständige
Windows-Build ist mit null Warnungen und Fehlern erfolgreich; Ruff und die betroffenen
Service-Typprüfungen bestehen ebenfalls.

Automatisiert geprüft werden:

- Linux-Auftrag aus einem frischen Prozess zum Socket in einem anderen Worker,
  einschließlich v2-Intent und persistentem Endstatus.
- Windows-Hinweis über eine Workergrenze, danach Lease, Intent und Endstatus;
  direkter Lease-Abruf ohne Benachrichtigung bleibt möglich.
- Vier konkurrierende Worker auf echter MariaDB: genau ein Linux-Transport und
  genau eine Windows-Lease, auch bei zwei bereiten Druckrouten.
- Redis-TCP-Unterbrechung und tatsächlicher Neustart eines dedizierten Containers,
  jeweils für Linux und Windows; doppelte Hinweise bleiben ohne zweiten Send.
- Worker-Neustart/abgelaufener Transport vor Socket-Send, Reconnect und Connection-
  Fencing, abgelaufene Zustellung nach möglichem Druck sowie alte Rückmeldungen.
- Lokaler Agent-Neustart während Intent, parallele Spool-Durchläufe, CUPS-Verzögerung,
  Offline-/Abbruchstatus, sicherer und unklarer Fehler, verlorene Status-ACKs,
  atomarer Windows-Outbox-Commit inklusive erzwungenem DB-Fehler und Rollback.
- Manuelle Auflösung mit genau einem neuen persistenten Auftrag.

Die vollständigen Client-Testläufe und der Windows-Cross-Build sind Bestandteil der
Prüfung. Die automatisierten Druckwege verwenden Fake-Spooler/CUPS-Backends. Der
Windows-Test, der einen installierten Browser verlangt, bleibt ohne passende lokale
Browserinstallation ausgenommen; dies wird im Testlauf als Skip ausgewiesen.

**Physische Abnahme offen:** Ohne ausdrücklich freigegebene Windows-/Linux-Testdrucker
konnte kein Papierausdruck geprüft werden. Für die Abnahme unter zwei und vier
Gunicorn-Workern sind je Plattform eindeutige Testseiten zu zählen; zusätzlich zwei
Windows-PCs am selben Drucker, Redis-/Worker-/Agent-Neustarts, verzögerter Spooler,
Drucker offline und manuelle Auflösung/Nachdruck prüfen. Original-ID, Generation,
Agent, Spoolerreferenz und tatsächliche Blattanzahl protokollieren. Bis dahin keine
produktive Freigabe; insbesondere bleibt auch die Alarm-SMS-Abnahme aus Phase 3 offen.

## Rollback

Neue Auslöser anhalten, aktive Jobs und lokale Journale abgleichen und sichern.
Einfaches Zurücksetzen von `uncertain`, `submitting`, `spooled` oder einem möglichen
Socket-Send auf `queued` ist kein Rollback. Bereits mögliche Ausgaben müssen manuell
geprüft werden. Eine Rücknahme der Cloud kann die additive Migration stehen lassen;
`0314 -> 0313` entfernt die neue Routingzuordnung und ist nur nach kontrolliertem Drain
geeignet. Die Migration wurde separat vorwärts und rückwärts geprüft.

Ein altes Linux-Binary würde die neue Intent-/Unsicherheitsgrenze umgehen und alte
Retry-Verfahren verwenden. Deshalb nur mit abgeglichenem Spool zurückwechseln und
keine Journale löschen. Windows-Journal und Cloud-Leases ebenfalls erhalten; ein
altes Binary kennt den neuen Nichtübergabe-Nachweis nicht. Gemischte Versionen sind
protokollkompatibel, besitzen aber jeweils nur die beschriebenen Sicherheitsnachweise.
Eine erhöhte Workerzahl wird erst nach der Geräteabnahme freigegeben.
