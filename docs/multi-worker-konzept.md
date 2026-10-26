# Einsatzcockpit: Multi-Worker-Konzept

Stand: 26.10.2026. Die Arbeiten liegen auf getrennten Feature-Branches; die produktive
Gunicorn-Konfiguration wurde nicht verändert. Die Bestandsbasis der Cloud ist
`faa41045`, der Windows-Agent wurde auf main `afbf064` geprüft.

## Ist-Architektur und ursprüngliche Risiken

| Verbindung | Lokaler Socket/Registry | Gemeinsamer Zustand und Routing |
| --- | --- | --- |
| Browser-WebSockets | `ConnectionManager` im Socket-Worker | Browser-Broadcasts über Redis; flüchtige Hinweise |
| Android SMS `/ws/sms-gateway` | `ws._sms_gateways`, Socket im Owner-Worker | SMS-Token-Presence und adressierte DB-/Redis-Queue; ab Phase 3 persistente Geschäftsjobs |
| Linux `/ws/gateway` | `ws._print_gateways`, Socket im Owner-Worker | Gateway-Presence, `PrintJob`, adressierter Transport; ab Phase 4 atomarer Push und lokale Intents |
| Windows `/ws/print-agent` | `ws._print_agents`, `_print_agent_sessions` | DB-Presence, Lease-/Intent-Verfahren, `job_available` über `CH_GW`, Polling-Fallback |

Die lokalen Registries sind keine workerübergreifende Verfügbarkeitsquelle. Vor Phase 2
konnte ein Auftrag auf einem Worker ohne passenden Socket scheitern; lokale Pending-
Futures erreichten Antworten auf anderen Workern nicht. Redis-Broadcasts allein lösen
weder diese Zuordnung noch die physische Deduplizierung. Ein erfolgreicher Publish oder
Socket-Send ist kein SMS-/Druckerfolg.

Windows besitzt bereits Lease, Generation, Intent, Resume, lokale SQLite-Outbox und
manuelle Behandlung unklarer Ausgaben. Die dokumentierten Windows-Protokollfixes
(Intent-IDs, Renewal, Download-Prüfung, PR #6) sind in der geprüften main-Basis
`afbf064` enthalten; der Cloud-Presence-Vorläufer ist Migration `0306`. Die neue gemeinsame
Presence verwendet diese vorhandenen Komponenten weiter. Neue additive Migrationen
`0312` bis `0315` gehören zu den Feature-Branches und werden hier nicht als bereits
produktiv ausgerollt behauptet.

Ursprünglich kritisch waren SMS-Antworten in lokalen Futures, erneutes Dispatchen ohne
Geschäfts-Claim, ein Queue-Handoff als vermeintlich gesendeter Alarm, Druck-Fallback
nach einem bloßen Timeout und erneute CUPS-Übergabe nach möglicherweise bereits erfolgter
Ausgabe. Worker-Neustarts konnten Verfügbarkeit, Antworten oder den Versandbeginn
vergessen. Die folgenden Phasen schließen diese Grenzen getrennt.

## Notwendige Änderungen und Umsetzungsstand

1. [Phase 2: Presence und Routing](multi-worker-phase2.md): eindeutige Connection-ID,
   Worker-ID, Tokenbindung, TTL und gefencete Disconnects. `gateway_message` persistiert
   adressierte Transporte; Redis beschleunigt, DB-Polling überbrückt Ausfälle.
2. [Phase 3: SMS](multi-worker-phase3.md): stabile Aufträge, atomare Claims, Lease und
   Generation, Empfang/Versuch/Annahme getrennt, persistente Ergebnisse, Android-Journal,
   nachweislich sicherer Provider-Fallback. Cloud-Commit `71c2714b`, Android `e5f11a3`.
3. [Phase 4: Drucken](multi-worker-phase4.md): atomarer Linux-Push, Cloud-/lokaler Intent,
   Status-Outbox, konservative Timeouts, Windows-Lease-/Status-Fencing und sichere
   manuelle Auflösung mit gezieltem Nachdruck. Client-Commits: Linux `5b1d2d5`,
   Windows `562c360`.

Sockets bleiben lokal. Alle mutierenden Geschäfts- und Presence-Zugriffe werden nach
Organisation und Ziel eingeschränkt. Eine alte Verbindung kann eine neuere nicht
offline setzen oder deren Auftrag übernehmen. Presence läuft nach einem Workerabsturz
auch ohne Sweep aus. Die SMS- und Linux-Jobloops dürfen in jedem Worker laufen; DB-Claims
entscheiden über die Übernahme. Die bestehenden organisationsweiten Hintergrund-Loops
in `app/main.py` verwenden weiterhin den Leader-Lock. Dieser ersetzt keine Job-Lease.
`deploy/einsatzleiter.service` bleibt in dieser Arbeit unverändert.

## Betroffene Dateien und Datenbank

Im Schwerpunkt: `app/routers/ws.py`, `ws_bus.py`, `gateway_presence.py`,
`gateway_routing.py`, SMS-/EUS-Dienste, Alarm-/API-Outbox, `linux_print_service.py`,
`print_dispatcher.py`, `print_agent_service.py`, `print_agent_presence.py`,
`print_watchdog.py`, `app/main.py` und die Gateway-/SMS-Modelle.
Clientseitig sind das Android-SMS-Plugin, das Linux-Print-&-Alarm-Gateway und der
Windows-Druckagent betroffen. Die Einzelphasen führen Dateien und Protokollfelder auf.

| Migration | Änderung |
| --- | --- |
| `0306` vorhanden | Windows-Connection-Presence |
| `0312` Phase 2 | Gemeinsame Owner-/Token-/TTL-Felder und adressierte Transportqueue |
| `0313` Phase 3 | SMS-Job, Versuche, Auswahlcursor und Protokoll-/Empfängerzuordnung |
| `0314` Phase 4 | Linux-Protokollfähigkeit und persistente Push-Transportzuordnung |
| `0315` Phase 5 | Ablaufende Worker-Heartbeats und gemeinsame Loop-/Redis-Diagnose |

Vorhandene Druckjobs, SMS-Logs, Tokens und Windows-Intent-/Lease-Daten bleiben erhalten.
Die tatsächlichen Client-Journale müssen ebenso gesichert werden wie die Cloud-DB.

## Kompatibilitätsrisiken, Tests und Rollback

Legacy-Gateways können weiter verbunden werden, besitzen aber weniger Nachweise. Ein
unklarer Ausgang darf ihre lokale oder serverseitige Wiederholung nicht als erfolgreich
erscheinen lassen. Neue `pending`-/`uncertain`-Zustände müssen API-Clients und Bediener
beachten. Alte Linux-Binaries enthalten lokale Wiederholungsrisiken, die allein durch
eine Cloud-Änderung nicht behoben werden. Ein gemischtes Cloud-Rollout alter und neuer
Alarm-Outbox ist vor dem kontrollierten Abgleich nicht freigegeben.

Die Tests verbinden echte WebSockets mit separaten Python-Prozessen, prüfen konkurrierende
Transaktionen auf MariaDB und starten ausschließlich dedizierte Redis-Testcontainer neu.
Client-Tests prüfen lokale Intents, Spoolergrenzen, Outbox und Recovery. Frühere Mock-Tests
mit lokalen Futures oder der Annahme „Timeout bedeutet sicher fehlgeschlagen“ wurden
auf die persistenten Grenzen umgestellt. Reale Modem-/Providerzustellung und Papierausgabe
bleiben eine eigene Abnahme; simulierte Backends können diese Lücke nicht schließen.

Für einen Rollback zunächst neue Auslöser stoppen, aktive und unklare Jobs samt lokalen
Journals abgleichen und sichern. Keine möglichen Außenwirkungen blind auf `queued`
zurücksetzen. Additive DB-Spalten können bei einem Code-Rollback stehen bleiben.
Datenbank-Downgrades erst nach Drain, Sicherung und geprüftem Statusabgleich ausführen.
Die Einzelphasen beschreiben die jeweiligen Grenzen. Produktive Freigabe und eine
Erhöhung auf zwei bis vier Gunicorn-Worker bleiben bis zur physischen Abnahme offen.

## Phase 5 und Nachprüfung

[Phase 5](multi-worker-phase5.md) ergänzt einen Fail-closed-Leader mit Übernahme,
Shutdown-Drain entkoppelter Tasks/Threads, Redis-Liveness und gemeinsame Betriebsdiagnose
auf der vorhandenen Systemstatus-Seite. Die vier in der Nachprüfung gefundenen Fehler
(alte `sendet`-SMS, Windows-Recovery bei Spoolerfehlern, Linux-CUPS-Unklarheit und
Linux-Abbruch während Download) sind dort mit Korrekturen und Regressionstests dokumentiert.

## Phase 6 und Aktivierungsvorschlag

[Phase 6](multi-worker-phase6.md) führt die isolierte Gesamtabnahme mit echten
Gunicorn-Workern, MariaDB, Redis, Browsern und simulierten Gateway-Protokollclients
zusammen. Die [Rollout-Anleitung](multi-worker-rollout.md) beschreibt die optionalen
Konfigurationsvorschläge und den Rückweg zu einem Worker. Geräteabnahme und
produktive Freigabe bleiben offen; die bestehenden produktiven Startdateien
wurden nicht verändert.
