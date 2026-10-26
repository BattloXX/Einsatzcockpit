# Multi-Worker, Phase 3: persistente SMS-Aufträge

Stand: 26.10.2026. Basis: Phase 2, Commit `79c67355`. Keine Änderung an
`deploy/einsatzleiter.service`, Workerzahl oder produktiven Einstellungen.

## Architektur und Zustandsnachweise

`app/services/sms_outbox.py` besitzt den verbindlichen Geschäftsstatus. Eine SMS erhält
beim ersten Einreihen eine zufällige, stabile Job-ID. `(org_id, job_key)` ist eindeutig;
ein Inhaltsfingerabdruck verhindert die Wiederverwendung mit anderem Text oder Ziel.
Alarm-SMS verwenden Einsatz-ID und Empfängerfingerabdruck als fachlichen Schlüssel.
GK-Versandaufträge verwenden außerdem die bestehende Versandzeilen-ID als stabilen Schlüssel.
Manuelle und API-SMS speichern Empfängersnapshots und Jobs in derselben Transaktion wie
ihren Protokolleintrag. Wiederkehrende Verleih-Erinnerungen verwenden dieselbe ID je
geplantem Erinnerungstermin. Nachversand verändert den gespeicherten Alarmtext nicht.

| Zustand/Nachweis | Bedeutung |
| --- | --- |
| `queued` | Auftrag dauerhaft gespeichert, kein Versand bestätigt |
| `claimed` | Atomarer Claim durch einen Worker; zufälliges Claim-Token, Generation und Lease |
| `dispatched` | Transport und ausgewähltes Gateway/Verbindung gespeichert; kein Versandnachweis |
| `acknowledged`, `received_at` | Gateway hat den Auftrag dauerhaft empfangen |
| `attempted_at` | Gateway meldet einen begonnenen Versandversuch; bei EUS vor HTTP-Aufruf gespeichert |
| `sent`, `accepted_at` | Alle SMS-Teile vom Modem beziehungsweise HTTP-Aufruf vom EUS-Provider angenommen |
| `failed` | Nachweislicher Nichtversand ohne weiteren konfigurierten Versandweg oder abgelaufene Queue |
| `uncertain` | Ein Versand könnte erfolgt sein; keine automatische Wiederholung |
| `delivery_status=unverified` | Tatsächliche Zustellung an den Empfänger ist nicht belegt |

Die Gateway-Empfangsbestätigung, der Versandversuch, die Modem-/Providerannahme und eine
Empfängerzustellung sind unterschiedliche Nachweise. Androids Send-Callbacks belegen
keine Empfängerzustellung. Eine Auswertung von Delivery-Receipts ist hier nicht implementiert.
Die Detailansicht zeigt die verfügbaren Zeitpunkte und benennt diese Grenzen ausdrücklich.

`sent`, `failed` und `uncertain` sind unveränderliche Endzustände. Späte positive Meldungen
lösen deshalb auch einen unklaren Auftrag nicht automatisch auf. Rückmeldungen benötigen
Organisation, aktives authentifiziertes Gateway, Job-ID und Generation. Die Legacy-Wire-ID
`<Job-ID>:<Generation>` ermöglicht auch alten Gateways eine eindeutige Zuordnung.

## Claim, Auswahl und Routing

Ein bedingtes DB-UPDATE übernimmt ausschließlich einen fälligen `queued`-Auftrag, erhöht
die Generation und setzt eine 60-Sekunden-Lease. Ein verspäteter Claim-Inhaber kann keine
neue Generation verändern. Alle Worker dürfen den SMS-Loop ausführen; ein Leader ist
hierfür nicht erforderlich. Nach einem reinen Claim-Absturz kann ein anderer Worker
übernehmen. Die Queuefrist beträgt eine Stunde; bei Alarm-SMS gilt die ursprüngliche
Alarmfrist, auch nach Migration eines älteren offenen Protokolls.

Die gemeinsame Gateway-Presence aus Phase 2 bestimmt das Ziel. Prioritätsreihenfolge,
Rotation, bevorzugtes Gateway und EUS-Reihenfolge bleiben erhalten. Ein nicht verfügbares
bevorzugtes Gateway blockiert andere erreichbare Gateways nicht. Ein Org-Cursor und die
Gateway-Sperren serialisieren Auswahl und die Grenze von sieben aktiven SMS je Gateway.
Die Auswahltransaktion verwendet auf MariaDB `READ COMMITTED`, damit wartende Worker
keine veraltete Kapazität aus einem früheren Snapshot lesen. SQLite verwendet dafür
`BEGIN IMMEDIATE`. Das sind lokale Transaktionsentscheidungen, keine Serverkonfiguration.

Statuswechsel und adressierter `gateway_message` werden gemeinsam committet. Redis
übermittelt anschließend den Hinweis auf `ws_bus.CH_GW`; nur der Socket-Worker sendet
über WebSocket. Die DB-Queue wird unabhängig davon alle 500 ms geprüft. Publisher-Echo,
doppelte Redis-Hinweise und paralleles Polling teilen denselben atomaren Transport-Claim.
Geschäftsergebnisse werden direkt im persistenten Job verarbeitet; ein lokales Future
beim auslösenden Worker ist nicht erforderlich. Ein Redis-Publish ist niemals SMS-Erfolg.
Eine Modembestätigung kann während eines Redis-Ausfalls über den DB-Pfad trotzdem korrekt
verarbeitet werden.

## Android-Protokoll und Wiederanlauf

Das Android-Repository erhält in Commit `e5f11a3` ein SQLite-Journal und die Fähigkeit `sms-transaction-v2`:

1. Cloud sendet `sms.prepare` mit stabiler ID, Generation und Nutzdaten.
2. Gateway persistiert den Empfang und sendet `sms.ack`; es sendet noch keine SMS.
3. Cloud persistiert die mögliche Außenwirkung zusammen mit `sms.commit`.
4. Gateway speichert vor dem Modemaufruf unwiderruflich seinen Versandbeginn. Dieselbe
   Freigabe kann dadurch höchstens einen Modemaufruf auslösen.
5. `sms.attempt` und `sms.result` werden aus dem Journal wiederholt. Die Cloud bestätigt
   verarbeitete und bereits abgeschlossene Ergebnisse mit `sms.result.ack`.

Das Journal trennt Cloud-/Kopplungstoken durch einen Fingerabdruck. Jeder Socket hat eine
lokale Verbindungsgeneration; alte Callbacks dürfen keine neue Verbindung überschreiben.
Beim Reconnect enthält Hello die offenen lokalen Jobs. Die Cloud kann eine Freigabe für
dieselbe Generation und dasselbe Gateway erneut zustellen; die lokale atomare Intent-Grenze
verhindert eine zweite SMS. Ein App-Neustart nach Versandbeginn erzeugt `uncertain`, wenn
keine vollständige Annahmebestätigung gespeichert wurde. Doppelte Multipart-Callbacks
ersetzen weder fehlende Teile noch ein bereits unklares Ergebnis. Zähler und UI-Ereignisse
für den Versand sowie der vorhandene SMS-Empfang bleiben erhalten.

## Wiederholung und Provider-Fallback

Ohne Freigabe kann ein v2-Auftrag nach Leaseablauf erneut vorbereitet werden. Nach einer
Freigabe ist dies nur zulässig, wenn **alle** zugehörigen Freigabe-Transporte nachweislich
noch ungesendet in der Queue stehen und atomar gesperrt werden können. Eine neue ungesendete
Reconnect-Freigabe beweist nicht, dass eine ältere Freigabe nie angekommen ist. Fehlen
Transportbelege, gilt das Ergebnis als unklar.

Legacy-Gateways erhalten weiter `sms.send`. Dessen Rückmeldung `ok=true` wird als
Modemannahme behandelt. Nach einem begonnenen oder unklar abgebrochenen Socket-Send ist
kein automatischer Gatewaywechsel zulässig. Ein negatives Legacy-Ergebnis enthält keinen
belastbaren Nichtversandnachweis und wird ebenfalls `uncertain`.

Fallback ist erlaubt, wenn kein Gateway verfügbar ist, wenn das v2-Gateway ausdrücklich
vor jedem Modemversuch ablehnt oder wenn EUS nachweislich nicht senden konnte. Dazu gehören
Konfigurations-/Authentifizierungsfehler und Verbindungsaufbaufehler. EUS-Read-Timeouts,
HTTP 500 und andere unklare Versandfehler lösen keinen Fallback aus. Ein ausgelastetes
Gateway ist kein Grund für einen Providerwechsel. Providerreihenfolge und Versuchshistorie
werden dauerhaft festgehalten; Zugangsdaten werden bei Ausführung aktuell aufgelöst.

Die Alarm-Outbox verwendet `retry` für den reinen Statusabgleich, bis alle Empfänger
angenommen oder endgültig bewertet sind. Ein Queue-Handoff gilt nicht mehr als gesendeter
Alarm. API-Empfänger werden nach einem Worker-Ausfall aus ihren verknüpften Jobs
fortgeschrieben. Der synchrone SMS-Endpunkt kann zusätzlich `pending` und `uncertain`
zurückgeben; API-Clients müssen diese Zustände auswerten und denselben Vorgang über seine
Auftrags-ID prüfen.

## Dateien und Datenbank

Migration `0313` erweitert `sms_gateway_token` um die ausgehandelte Protokollversion,
`sms_log_recipient` um Job-Zuordnung und projizierten Status sowie
`api_message_recipient` um die Job-Zuordnung. Neu sind:

- `sms_dispatch_job`: mandantengetrennter Auftrag, Inhalt/Fingerabdruck, Lease, Generation,
  Providerfortschritt, Gateway/Verbindung und Nachweiszeitpunkte.
- `sms_dispatch_attempt`: jede Generation mit Transport-/Freigabe-ID und Ergebnis.
- `sms_dispatch_cursor`: organisationsbezogener Auswahlcursor.

Angefasst sind die SMS-Modelle, Tenant-Registrierung, Startup/Shutdown, WebSocket-Router,
SMS-/EUS-Dienste, Alarm- und API-Outbox, manuelle Versandroute, Detailansicht und Retention.
Im Android-Repository: `SmsGatewayService`, `SmsDispatchJournal`, Protokolltests und deren
Gradle-Testabhängigkeiten. Es werden keine Funktionen oder Protokolle entfernt.

Die bestehende organisationsbezogene SMS-Retention entfernt auch die neuen Kopien von
Rufnummer und Text abgeschlossener Jobs. Ein schlanker Tombstone mit Inhaltsfingerabdruck,
ID und Ergebnis bleibt gegen Wiederholung erhalten. Aktive Jobs werden durch die
Log-Retention nicht aus der Nachverfolgung entfernt. Das Gateway entfernt Ziel und Text
beim lokalen Abschluss; lokale Dedup-Tombstones bleiben erhalten. DB-Backups und das
Gateway-Journal gehören damit weiterhin zum Schutzumfang der SMS-Daten.

## Prüfung und Freigabe

Der breite Regressionslauf besteht mit **278 Tests**, zwei Redis-Varianten werden ohne
Testcontainer-Konfiguration übersprungen. Die explizite Redis-Neustartprüfung wurde separat
erfolgreich ausgeführt. Die sieben Android-Journaltests bestehen ebenfalls.

Automatisierte Prüfungen umfassen Generation-/Tenant-/Token-Fencing, doppelte ACKs,
Reclaim vor Freigabe, unklare Ergebnisse nach möglichem Versand, Reconnect, sichere und
unsichere Provider-Fallbacks, parallele verschiedene Jobs, Prioritäten, Retention und
bestehende SMS-/API-/Alarmabläufe. Echte MariaDB-Transaktionen prüfen vier konkurrierende
Claim-Inhaber, erstmalige Idempotenz-Key-Anlage und die gemeinsame Gateway-Kapazität.
Die Migration wurde vollständig auf einer separaten MariaDB bis `0313` sowie zurück auf
`0312` und erneut vorwärts ausgeführt; zusätzlich existiert ein additiver Migrationstest.

`tests/test_sms_alarm_multiworker.py` führt den Alarm mit echter WebSocket-Verbindung auf
einem Worker und Auslöser in einem frischen Python-Prozess aus. Der Test bestätigt Empfang
und Versand getrennt, simuliert einen Abbruch nach Freigabe und startet einen dedizierten
Redis-Container tatsächlich neu. Ausführung der Redis-Variante:

```sh
REDIS_RESTART_TEST_URL=redis://127.0.0.1:26386/0 \
REDIS_RESTART_TEST_CONTAINER=ecp-sms-phase3-redis \
python -m pytest -q tests/test_sms_alarm_multiworker.py
```

**Keine produktive Freigabe:** Der Protokoll-Peer ersetzt das Modem. Vor Freigabe sind
Alarm-SMS mit freigegebenem Android-Testgerät und realer Zielnummer unter 2 und 4 Gunicorn-
Workern zu prüfen, einschließlich Redis-/Worker-/Gateway-Neustart, Multipart-SMS,
Gateway-Priorität und EUS-Fallback ohne Doppel-SMS. Ein Testprotokoll muss die tatsächliche
Empfängerzustellung getrennt von der Modem-/Providerannahme dokumentieren. Dafür wurden
hier keine produktiven Geräte, Nummern oder Einstellungen verwendet.

## Rollback

Zunächst alle neuen SMS-Auslöser anhalten, aktive Jobs abgleichen und DB plus lokale
Gateway-Journale sichern. Für einen reinen Code-Rollback die additive Migration stehen
lassen. Bereits mögliche Außenwirkungen und `uncertain` dürfen nicht auf `queued`
zurückgesetzt werden. Alte Cloud-Versionen kennen die neuen Jobs nicht und dürfen sie
nicht aus Alarm-/API-Protokollen erneut erzeugen; deshalb nur nach kontrolliertem Drain
beziehungsweise manuellem Abgleich zurückwechseln.

Ein Android-Rollback erst nach dem Abgleich aller lokalen Intents; Journal und Kopplung
nicht löschen, um einen vermeintlich sauberen Zustand herzustellen. Das neue Gateway
versteht weiterhin das alte `sms.send`, die neue Cloud bedient alte Gateways konservativ.
Ein gemischtes Cloud-Rollout mit alter und neuer Alarm-Outbox ist nicht freigegeben.
`alembic downgrade 0312` entfernt die neue Historie und ist ausschließlich nach gesichertem,
abgeschlossenem Abgleich zulässig; für einen schnellen Rollback ist es ungeeignet.


## Sicherheitskorrektur nach der Nachprüfung

Mit [Phase 5](multi-worker-phase5.md) werden alte, nicht zugeordnete `sendet`-
Empfänger konservativ als persistente `uncertain`-Jobs übernommen, auch außerhalb
des Nachversandfensters. Eine abgelaufene alte Lease beweist keinen Nichtversand.
Diese Empfänger lösen keinen neuen Gateway-/Providerauftrag aus. Nachweislich noch
wartende Alt-Empfänger bleiben innerhalb der Alarmfrist versendbar. Bestehende
terminale Jobs und ihre stabile ID werden bei der Zuordnung erhalten.
Regressionen prüfen alte und aktuelle Logs, bekannte terminale Ergebnisse,
mehrfachen Nachversand und fehlende automatische Sends. Die Geräteabnahme bleibt offen.
