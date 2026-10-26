# Multi-Worker-Rollout und Rollback: Vorschlag

Stand: 26.10.2026. **Nicht produktiv aktiviert, keine Freigabe.** Die
[Phase-6-Abnahme](multi-worker-phase6.md) enthält die Testergebnisse und offenen
Geräte-/Funktionsnachweise. Alle folgenden Betriebsschritte sind zur Prüfung
durch den Betreiber bestimmt und wurden nicht auf Produktion ausgeführt.

## Aktueller Start und optionaler Vorschlag

| Betrieb | Aktuell im Repository | Optionaler Vorschlag |
| --- | --- | --- |
| Systemd | `deploy/einsatzleiter.service`, Benutzer `clp-einsatz`, `.env` als EnvironmentFile, Gunicorn `-w 1`, Bind `127.0.0.1:8092` | Drop-in unter `deploy/proposals/multi-worker/einsatzleiter.service.d/worker.conf` |
| Docker | `Dockerfile` startet Gunicorn `-w 1`, Bind `0.0.0.0:8092`; Compose veröffentlicht nur `127.0.0.1:8092`, MariaDB 12.3, Redis 7, persistente Volumes | Explizites zweites Compose-File `deploy/proposals/multi-worker/docker-compose.override.yml` |

Die vorhandenen drei Dateien bleiben unverändert. Der alte Hinweis in der
Systemd-Datei über rein lokalen SMS-Versand beschreibt die produktive main-Basis;
die neuen Garantien gelten erst nach Integration der vollständigen Phasen und
nach Abnahme der passenden Clients.

Beide Vorschläge verwenden `deploy/proposals/multi-worker/gunicorn.conf.py` mit
`ECP_GUNICORN_WORKERS=1` als sicherem Standard. Zulässig sind ausschließlich
`1`, `2`, `3`, `4`; mehrere Worker verlangen `REDIS_URL`. Das prüft Konfiguration,
nicht tatsächliche Erreichbarkeit: Letztere muss die Systemstatus-Seite bestätigen.
Workerklasse, Request-Timeout 120 Sekunden und Keepalive 5 bleiben erhalten.
Der Vorschlag verlängert außerdem Gunicorns Graceful-Timeout von standardmäßig
30 auf 120 Sekunden für den geordneten Shutdown. Die optionalen Systemd-/Docker-
Stopfristen sind deshalb auf 150 Sekunden gesetzt und am Zielhost zu prüfen;
ein früherer harter Abbruch ist weiterhin
über Claims/`uncertain` abgesichert, aber kein erfolgreicher Drain.
Der Accesslog enthält Methode/Pfad ohne Querystring oder Referer.

## DB-Verbindungsbudget

`DB_POOL_SIZE` und `DB_MAX_OVERFLOW` gelten je Worker (Standard: 10/30).
Empfehlung: 2 Worker mit 10/20, 3 mit 8/15, 4 mit 8/12.
Der Gunicorn-Vorschlag warnt beim Master-Start, wenn
`Worker * (Pool + Overflow) + 20 Reserve > DB_MAX_CONNECTIONS` (Standard: 151).
Die Reserve ist fuer Wetter-DB, Migrationen und Administration einzuplanen;
weitere Anwendungen auf demselben DB-Server brauchen zusaetzliches Budget.
Die Warnung verhindert den Start nicht.

Am Zielhost MariaDB mit `SHOW VARIABLES LIKE 'max_connections';` pruefen und
`DB_MAX_CONNECTIONS` auf das tatsaechliche Limit setzen. Poolwerte und Limit
als Prozess-Umgebungsvariablen auch dem Gunicorn-Master bereitstellen.
Bei Bedarf MariaDB bewusst hoeher konfigurieren, etwa im Compose-Override
beim DB-Service mit `command: ["--max-connections=300"]`; Speicherbedarf und
vorhandene Startoptionen dabei beruecksichtigen. `DB_MAX_CONNECTIONS` allein
aendert das MariaDB-Limit nicht.

## Voraussetzungen und Freigabegrenzen

1. Vollständige Cloud-Änderungen der Phasen 2–6 samt Review integrieren. Ein
   Phase-6-Commit allein auf der ursprünglichen main-Basis reicht nicht.
   Migrationen `0312`, `0313`, `0314`, `0315` und die dokumentierten
   Android-/Linux-/Windows-Änderungen müssen enthalten sein. `alembic heads` und
   installierte Clientversionen überprüfen; diese Arbeit behauptet keine bereits
   erfolgte main-Integration oder Bereitstellung.
2. DB-Backup und lokale Gateway-Journale sichern. Offene/unklare SMS und Drucke
   mit Job-ID, Generation, Gateway und Intent abgleichen. Geheimnisse weder in
   Tickets noch in Diagnoseausgaben kopieren.
3. Alle Worker müssen dieselbe DB, Redis-Instanz, Konfiguration und Codeversion
   verwenden. Ein gemeinsamer absoluter `LEADER_LOCK_PATH`, passende Rechte und
   derselbe Inode auf einem Host sind verpflichtend. Keine App-Replikate mit
   getrennten Locks und kein Multi-Host-Rollout mit diesem Dateilock.
4. Physische Alarm-SMS-End-to-End-Abnahme, Gateway-Abbruch und kontrollierter
   Provider-/EUS-Fallback mit autorisierten Testnummern durchführen. Windows und
   Linux physisch drucken, zwei PCs am selben Netzwerkdrucker samt Failover und
   unklarer Spoolerübergabe prüfen. Annahme durch Provider und tatsächliche
   Empfängerzustellung getrennt protokollieren. Ohne diese Nachweise kein Go.
5. Offene Funktionsrisiken aus Phase 6, insbesondere Lagekarten-/Yjs-Kollaboration,
   TTL-Erkennungsfenster und externe Leader-Dienste, bewerten beziehungsweise
   vor Nutzung ergänzend testen. Zielhost-Versionen und Ressourcen nachweisen.

## Stufe 1: zwei Worker im Test

Die isolierte Suite mit `--ecp-workers 2` ausführen und das Ergebnisprotokoll
prüfen. Anschließend dieselbe freizugebende Anwendung und reale Gateways in der
Testumgebung prüfen. Keine fremden Container oder produktiven Redis-Dienste für
Ausfalltests neu starten. Erwartet werden zwei frische Worker, genau ein
Leader, Redis verbunden, tenantgetrennte Gateway-Presence und kein dauerhaft
überfälliger Job. `uncertain` erfordert manuelle Auflösung mit Gerätenachweis;
der Status darf nicht zum Erzeugen eines grünen Monitors gelöscht werden.

Die bestehende Dienstüberwachung/Systemstatus-Seite bleibt maßgeblich. Nach
jedem Neustart Leader, letzten erfolgreichen Loop-Durchlauf, Gateway-Heartbeats,
offene Jobs, überfällige ACKs und unklare Aufträge prüfen. Zeitlich begrenzte alte
Heartbeat-Einträge von echten parallel laufenden Leader-Aufgaben unterscheiden.

## Stufe 2: zwei Worker in Produktion, nur nach explizitem Go

Vor einem Versionswechsel neue Auslöser kontrolliert pausieren, laufende Jobs
abgleichen und alle alten Worker sauber beenden. Nicht alte und neue
Leader-/Outbox-Implementierungen parallel starten. Migrationen nach Sicherung
und vor Start der neuen Cloud ausführen. Keine lokale Agent-Outbox löschen.

**Systemd-Vorschlag:** Den genannten Drop-in nach Prüfung von Benutzer,
Arbeitsverzeichnis, Pfaden und Stopfrist unter
`/etc/systemd/system/einsatzleiter.service.d/worker.conf` installieren. Der
vorhandene EnvironmentFile-Eintrag kann `Environment=` aus dem Drop-in
überschreiben: daher Workerzahl und Lockpfad in der Ziel-`.env` ausdrücklich
prüfen. Gewollt sind `ECP_GUNICORN_WORKERS=2`, ein erreichbares `REDIS_URL` und
der gemeinsame absolute Lockpfad. Anschließend Dienstdefinition prüfen,
`daemon-reload` und kontrollierten Neustart ausschließlich durch den Betreiber
ausführen. Den wirksamen Startbefehl prüfen, ohne Umgebungsgeheimnisse auszugeben.

**Docker-Vorschlag:** Image muss die Konfigurationsdatei enthalten; ein älteres
Registry-Image wird durch ein Compose-Override nicht aktualisiert. Einen
unveränderlichen geprüften Image-Tag verwenden und `.env` auf
`ECP_GUNICORN_WORKERS=2` und das interne `REDIS_URL` abstimmen. Erst Konfiguration
prüfen; folgende Befehle sind Vorschläge für den Betreiber:

```sh
docker compose -f docker-compose.yml \
  -f deploy/proposals/multi-worker/docker-compose.override.yml config --quiet
docker compose -f docker-compose.yml \
  -f deploy/proposals/multi-worker/docker-compose.override.yml up -d --no-deps app
```

Die vollständige Ausgabe von `docker compose config` kann Secrets enthalten und
gehört nicht in Logs/Tickets. Stopfrist vor dem Wechsel prüfen. DB-/Redis-Volumes
erhalten; kein `down -v`. Ein App-Container mit zwei Prozessen ist hier gemeint,
keine Erhöhung der Containeranzahl. Gateway-Reconnects und Statusabgleich abwarten,
dann kontrollierte Testalarme/-drucke und Monitoring prüfen.

## Stufe 3: Last mit drei und vier Workern

Erst nach erfolgreicher, ausdrücklich freigegebener Produktionsstufe mit zwei
Workern in einer isolierten Lastumgebung testen. `ECP_GUNICORN_WORKERS` lässt
beide Werte zu; die vollständige Phase-6-Suite jeweils erneut ausführen und
repräsentative Alarm-/Browser-/PDF-Last ergänzen. SMS-/Druck-Zähler und
`uncertain` neben Durchsatz, P95/P99-Latenzen, CPU, RAM, DB-Verbindungen und
Queuealter auswerten. Jede physische Last braucht eigene Testziele und Freigabe.
Es gibt in Phase 6 keine Lastfreigabe für drei oder vier Worker.

## Rollback auf einen Worker

Bei Störung neue Auslöser pausieren und aktuelle Job-/Gateway-Journale sichern.
Ein nachvollziehbarer `uncertain`-Status bleibt erhalten. Keine SMS, Druckjobs,
Leases oder Generationen blind auf wartend zurücksetzen. Zuerst die neue
Cloud-Version mit **einem** Worker weiterverwenden:

- Systemd: `ECP_GUNICORN_WORKERS=1` im tatsächlich wirksamen EnvironmentFile
  setzen und den Dienst kontrolliert neu starten. Alternativ den geprüften
  Originalstart mit `-w 1` wiederherstellen. Den gemeinsamen Lockpfad erhalten.
- Docker: `ECP_GUNICORN_WORKERS=1` setzen und App mit denselben beiden Compose-
  Dateien neu erstellen. Alternativ mit ausschließlich der Basisdatei starten:
  das vorhandene Image muss den Original-CMD mit `-w 1` besitzen. Den geänderten
  Startbefehl auch tatsächlich durch Recreate übernehmen, nicht nur `restart`.

Danach einen frischen Worker und Leader, Redis, Gateway-Reconnects und alle
ausstehenden Jobs prüfen. Ein Worker benötigt kein DB-Downgrade; Tabellen und
Intents bleiben erhalten. Die komplette Protokollsuite wurde hierfür separat
mit einem Worker erfolgreich ausgeführt, einschließlich Neustarts und Ausfällen.

Ein zusätzlicher Code-/Client-Rollback ist ein eigener, riskanterer Schritt:
ältere Stände enthalten die behobenen Doppelversand-/Recovery-Fehler und verstehen
neue Zustände unter Umständen nicht. Nur nach Drain, Statusabgleich, Sicherung
und Review vorgehen. Datenbank-Downgrades niemals allein zum Reduzieren der
Workerzahl ausführen. Manuelle Auflösung oder gezielter Nachdruck erfolgen über
die bestehenden nachvollziehbaren Bedienverfahren mit Geräte-/Providerbeleg.
