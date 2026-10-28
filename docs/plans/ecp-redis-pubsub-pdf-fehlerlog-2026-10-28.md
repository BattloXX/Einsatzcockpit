# ECP – Redis-Pub/Sub-Abbrüche, PDF-Auslagerung, Fehlerlog-Helfer (2026-10-28)

Stand: gegen den echten Code geprüft (`app/services/ws_bus.py`, redis-py 8.1.0 aus `.venv`).
Kein lokales `redis-server` vorhanden: Tests laufen mit fakeredis und einem steuerbaren
Pub/Sub-Double; der 30-Minuten-Lauf findet nur auf dem Test-ECP statt.

## Teil A – Redis-Pub/Sub (`subscriber_disconnected`)

### Befund gegen den Code

Gesichert (Quelltext von redis-py und ECP):

1. `pubsub.get_message(timeout=1)` wirft bei Stille nicht. `Connection.read_response`
   gibt bei einem vom Aufrufer gesetzten Timeout `None` zurück; `socket_timeout=5` gilt
   dort nicht. Ein ausbleibender Nachrichteneingang allein ist also kein Fehler.
2. `_failure()` loggt jeden Code höchstens alle 30 s. Das "etwa alle 30 Sekunden" ist
   vermutlich die Logdrosselung, nicht die echte Abbruchfrequenz. Maßgeblich ist
   `_diagnostics["subscriber_failures"]`; die Ausnahmeklasse wird bewusst nicht
   protokolliert und ist deshalb heute nicht erkennbar.
3. Zwei Heartbeats laufen auf derselben Verbindung: redis-py sendet bei
   `health_check_interval=5` selbst ein PING (`check_health`, pro `get_message` sobald
   fällig, Antwort wird verschluckt) und `_reader` sendet zusätzlich `pubsub.ping()` mit
   5 s Antwortfrist (`HEALTH_TIMEOUT_S`) bei 1-s-Raster.
4. `_reader` ruft `await _redis.ping()` auf dem gemeinsamen Publisher-Client auf.
   Scheitert dieser (`socket_timeout=5`, Event-Loop-Verzug, Pool), wird der
   Subscriber abgebaut, obwohl dessen Verbindung gesund ist. Publisher und Subscriber
   sind gekoppelt: `_supervise` schließt `_redis` mit.
5. `_dispatch` führt die Handler inline im Leseloop aus. Ein langsamer Handler
   (Broadcast, DB-Zugriff) oder ein blockierter Event-Loop verzögert die PONG-Verarbeitung;
   ab 5 s ohne verarbeitetes PONG folgt `subscriber_heartbeat_timeout`.
6. Exponentieller Backoff existiert (0,1 s … 5 s), wird aber nach jedem erfolgreichen
   `_connect` auf 0,1 s zurückgesetzt, auch wenn die Verbindung sofort wieder fällt.

Hypothesen (erst nach Schritt R1 belegbar): dominierende Ursache ist 4 oder 5 in
Kombination mit den ca. 2 s Loop-Verzug; die Heartbeat-Parameter allein (3) sind bei
unverzögertem Loop unkritisch.

### Schritte (nur Test-Branch, Produktionskonfiguration unverändert)

- R1 Diagnose zuerst: `_failure` bekommt Ausnahmeklasse (kein Text), Verbindungsdauer,
  Zeitpunkt des letzten PONG, Reconnect-Zähler, Worker-ID. Keine URL, kein Passwort.
  `status()` liefert die Felder; Test prüft, dass keine Zugangsdaten auftauchen.
- R2 Getrennte Verbindungen: eigener Publisher-Client (`socket_timeout` 5, kurzes
  `socket_connect_timeout`) und eigener Subscriber-Client (`socket_timeout=None`, Lesen
  nur über `get_message(timeout=1)`). Der Subscriber ruft nie den Publisher-Client auf;
  ein Publisher-Fehler baut den Subscriber nicht ab und umgekehrt.
- R3 Ein Heartbeat: redis-py-`health_check_interval` am Subscriber auf 0, nur der eigene
  PING/PONG bleibt, mit Frist 15 s (Vielfaches des Intervalls 5 s). Stille wird nie als
  Fehler gewertet, nur ein ausbleibendes PONG. Frist und Intervall als Modulkonstanten.
- R4 Handler entkoppeln: `_dispatch` stellt Nachrichten in eine begrenzte `asyncio.Queue`,
  ein eigener Task arbeitet sie ab. Der Leseloop verarbeitet PONGs unabhängig von
  Handlerlaufzeiten. Überlauf: ältester Hinweis wird verworfen und gezählt (Hinweise sind
  nur Signale, Zustellung liegt in der DB).
- R5 Backoff: Reset auf 0,1 s erst nach 30 s stabiler Verbindung, Jitter, Obergrenze 5 s.
- R6 Zwei Worker unabhängig: Test mit zwei Bus-Instanzen auf demselben fakeredis-Server;
  Nachricht von A erreicht lokale Clients beider, Ausfall eines Subscribers beeinflusst
  den anderen nicht. Leader-Wahl bleibt unverändert (nur Prüfung der Abnahme).
- R7 Event-Loop-Verzug: Watchdog aus PR #765 liefert jetzt Taskname und Stack. Auf dem
  Test-ECP auswerten, blockierende synchrone Aufrufe benennen (Kandidaten: PDF, sync DB
  in `async def`, Such-Indexer). Erst dann gezielt beheben.
- R8 Tests (`tests/test_ws_bus_stabilitaet.py`): Normalbetrieb ohne Nachrichten über
  mehr als drei Heartbeat-Intervalle ohne Reconnect (virtuelle Uhr); Redis-Neustart und
  Verbindungsabbruch mit Wiederverbindung und Re-Subscribe; verzögertes PONG unterhalb
  und oberhalb der Frist; langsamer Handler löst keinen Abbruch aus; Publisherfehler
  trennt den Subscriber nicht; Diagnosefelder ohne Zugangsdaten; doppelte Hinweise
  führen nicht zu doppelten SMS-/Alarm-/Druckaufträgen (Zustellung bleibt DB-Claim).

### Abnahme (Test-ECP)

Zwei Gunicorn-Worker, genau ein Leader, 30 Minuten ohne `subscriber_disconnected`,
Redis-Stopp/-Start wird erkannt und behoben, workerübergreifende WebSocket-Ereignisse,
keine doppelten Aufträge. Auswertung über `status()` beider Worker.

### Vorgehen und Grenzen

Branch + PR, **kein Merge und kein Produktiv-Deploy** vor der Abnahme auf dem Test-ECP
(ausdrückliche Vorgabe, überstimmt die übliche Merge-Routine). Die Produktions-`.env`
und Gunicorn-Konfiguration bleiben unverändert; alle Werte sind Codekonstanten mit den
bisherigen Defaults als Obergrenze.

## Teil B – PDF-Erstellung vom Event-Loop nehmen (P3)

- Ist: WeasyPrint-`write_pdf` in `pdf_service.py` (9 Stellen), `print_artifact_service.py`,
  `katplan_pdf_service.py`, `objekt_pdf_service.py`, `lagefuehrung_pdf_service.py`,
  `foerderstrecke_pdf_service.py`, `uas_pdf.py`. Aufrufer in `async def` blockieren den Loop;
  sync-Routen (`def`) laufen bereits im Threadpool.
- B1 Inventur: je Aufrufer prüfen, ob er aus `async def` erreicht wird (Test
  `test_async_routes_sync_db` als Anhaltspunkt, plus Grep auf Aufrufer).
- B2 Gemeinsamer Helfer `pdf_render.render_async(fn, *a)` mit
  `asyncio.to_thread` und Semaphore (Standard 2, wegen Speicher) sowie Zeitmessung.
  Keine neue Modulzustandsliste außer der Semaphore (Allowlist mit Begründung).
- B3 Nur `async def`-Aufrufer umstellen; sync-Aufrufer bleiben. Test: Loop bleibt während
  eines künstlich langsamen Renders ansprechbar; Semaphore begrenzt Parallelität.
- Risiko: WeasyPrint ist nicht garantiert threadsicher bei gemeinsamem Zustand; pro
  Aufruf eigene `HTML`-Instanz (ist bereits so). Fehler durchreichen unverändert.

## Teil C – Fehlerlog-Helfer (L4)

- C1 `app/core/fehlerlog.py`: `log_fehler(logger, ereignis, exc, *, dauer_s=None, **kontext)`;
  schreibt Ausnahmeklasse, Dauer, Worker-ID, Korrelations-ID. Kein `str(exc)`, keine
  Tokens, Telefonnummern, SQL-Parameter, URLs mit Zugangsdaten (Allowlist erlaubter
  Kontextschlüssel).
- C2 Korrelations-ID als `ContextVar`, gesetzt in der Request-Middleware (vorhandene
  Request-ID wiederverwenden, falls vorhanden); Hintergrundtasks setzen eigene ID.
- C3 Einführung zuerst in `ws_bus`, `ws.py` (Gateway), Teams-/Alarm-Outbox; keine
  Massenumstellung.
- C4 Tests: Pflichtfelder vorhanden, Geheimnisse nie im Log (Token, Telefonnummer,
  Passwort in Ausnahmetext), ContextVar-Isolation parallel laufender Tasks.

## Reihenfolge

1. PR H: Fehlerlog-Helfer (C) – wird von R1 gebraucht.
2. PR I: Redis R1–R6, R8 (Test-Branch, nicht mergen bis Abnahme).
3. PR J: PDF-Auslagerung (B).
4. R7 nach Auswertung der Watchdog-Logs des Test-ECP.

## Offene Entscheidungen

- Frist für ausbleibende PONGs (Vorschlag 15 s) und Queue-Größe (Vorschlag 1000).
- PDF-Parallelität (Vorschlag 2); abhängig vom RAM des Servers.
