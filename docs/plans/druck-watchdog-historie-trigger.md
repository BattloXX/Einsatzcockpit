# Plan: Die vier offenen Punkte am Druck-Gateway schliessen

Repo `/home/johannes/projects/Einsatzcockpit`, Basis `main` @ `cbd1af3`.
Von Codex gegengeprueft; die Befunde sind eingearbeitet.

## Context

Nach PR #282 (Druckregeln als eigenes Tab) und #283 (Fallback-Drucker, Testdruck, Renderfehler)
blieben vier Punkte bewusst offen. Sie werden jetzt geschlossen. Zwei Produktentscheidungen
tragen den Zuschnitt:

- **Haengende Jobs**: Der Watchdog eskaliert sie auf `failed` — das greift in den Fallback aus #283.
- **Tote Ausloeser**: Nachzuegler-Semantik. Der Idempotenzschluessel bleibt unangetastet; es wird
  nur gedruckt, was noch nicht gedruckt wurde.

Ein Muster zieht sich durch alle vier: angelegte Infrastruktur ohne Verbraucher. Der
Fallback-Drucker war so ein Fall (#283), weitere bleiben auch danach bestehen (siehe
„Ausserhalb des Scopes").

**Keine Migration noetig** — kein Punkt braucht eine Schemaaenderung. Triggerwerte,
Dokumenttypen und Spalten existieren; der Druckerstatus liegt in einem JSON-Feld.

---

## Punkt 1 — Haengende Druckauftraege eskalieren

**Problem.** `dispatch_print_job` (`app/routers/ws.py:872-933`) liefert beim Timeout
`{"status": "sent", "note": "timeout_waiting_status"}`. `dispatch_job`
(`app/services/print_dispatcher.py:193-215`) verwirft das `note` vollstaendig. Nur `RuntimeError`
(„kein Gateway") fuehrt zu `JOB_FAILED`. Der Job haengt unbegrenzt in `sent`: kein Retry, kein
Fallback, kein UI-Signal. Einziger Zeuge ist `print_watchdog._warn_stale_print_jobs` — und der
loggt nur.

**Loesung.** In `app/services/print_watchdog.py`:

```python
STALE_AFTER_MINUTES = 10       # bleibt: ab hier warnen
ESCALATE_AFTER_MINUTES = 30    # neu: ab hier auf failed setzen
```

Die Schwelle liegt bewusst deutlich ueber dem dokumentierten Gateway-Retry (5x/10 min,
`docs/plans/ecpg-gateway-plan.md:171`), damit ein spaet gemeldetes `done` nicht ueberholt wird.
Einschraenkung, die in den Kommentar gehoert: das ist eine **Plan-Angabe**; die tatsaechliche
Retry-Konfiguration des Gateway-Containers ist aus diesem Repo nicht verifizierbar.

### Die Uebernahme muss atomar sein

`mark_job_failed` (`print_dispatcher.py:219-231`) prueft in Python und committet danach — das ist
**kein** Compare-and-set. Zwei Worker, die denselben Job als `sent` gelesen haben, setzen beide
erfolgreich auf `failed` und bekommen beide `True`. Der Watchdog-Loop laeuft heute ohne
Leader-Guard in jedem uvicorn-Worker, also ist das ein reales Rennen.

Neue Funktion in `print_dispatcher.py` mit bedingtem UPDATE, das nur der Gewinner uebernimmt:

```python
def escalate_stale_job(db: Session, job_id: int, grund: str) -> bool:
    """Uebernimmt einen haengenden Job atomar auf 'failed'. True nur fuer den Gewinner.

    Bedingtes UPDATE statt Python-Pruefung: der Watchdog laeuft ohne Leader-Guard in
    jedem Worker, ein Check-then-commit wuerde doppelt zuschlagen.
    """
    treffer = (
        db.query(PrintJob)
        .filter(PrintJob.id == job_id, PrintJob.status.in_((JOB_SENT, JOB_PRINTING)))
        .update({"status": JOB_FAILED, "error": grund[:500]}, synchronize_session=False)
    )
    db.commit()
    return treffer == 1
```

Achtung `CLAUDE.md`: Bulk-`update()` auf Tenant-Tabellen laeuft ungefiltert. Hier ist das
zulaessig, weil ueber den Primaerschluessel gefiltert wird — das ist im Docstring festzuhalten.

`mark_job_failed` bleibt fuer den Renderfehler-Pfad unveraendert; nur der Watchdog nutzt die
neue Funktion. Die Unique-Key-Deduplizierung des Ersatzdrucks
(`print_dispatcher.py:127-166`) bleibt als **zweite** Schutzschicht bestehen, ist aber nicht mehr
die Begruendung.

### Ablauf im Loop

- `_warn_stale_print_jobs` wird zu `_pruefe_haengende_jobs() -> list[int]`: warnt wie bisher ab
  `STALE_AFTER_MINUTES`, eskaliert ab `ESCALATE_AFTER_MINUTES` per `escalate_stale_job` und gibt
  die **tatsaechlich uebernommenen** IDs zurueck.
- `print_job_watchdog_loop` (`:51-63`) danach je ID:
  1. `await broadcast_org(job.org_id, {"type": "print_job_status", "job_id": …, "status": "failed", "gateway_id": …})`
  2. `await dispatch_fallback_for_failed_job(job_id)`

  Der Broadcast ist **nicht optional**: Statusmeldungen entstehen heute nur bei
  Gateway-Rueckmeldungen (`ws.py:664-678`) und beim Abbruch (`ui_gateway.py:777-789`). Ohne ihn
  bliebe die neue Live-Historie aus Punkt 2 nach einer Eskalation veraltet.
- Der Sync-Teil bleibt in `asyncio.to_thread` (Vorgabe im Modulkommentar
  `app/services/loop_utils.py:3-5`), der async-Teil laeuft danach im Loop.
- `dispatch_job`: das verworfene `note` wenigstens loggen (`logger.info`, **kein** `job.error` —
  Spoolen ohne sofortige Rueckmeldung ist der Normalfall und darf in der Historie nicht wie ein
  Fehler aussehen).

**Annahme, die zu dokumentieren und zu testen ist:** `aktualisiert_am` ist ein
ORM-`onupdate`-Zeitstempel (`app/models/gateway.py:314-317`) und bedeutet „letzte Jobaenderung",
nicht „Beginn des Gateway-Retry". Fuer die vorhandenen Pfade ist das ausreichend.

**Verbleibendes Risiko:** Meldet das Gateway nach der Eskalation doch noch `done`, verwirft der
Terminal-Schutz aus #283 diese Meldung — der Job bleibt faelschlich `failed` und der Ersatzdruck
ist gelaufen. Die 30 Minuten sind die Absicherung, mehr nicht.

---

## Punkt 2 — Druckhistorie live

**Problem.** `broadcast_org(org_id, {"type": "print_job_status", …})` wird bereits gesendet
(`ws.py:670`, `ui_gateway.py:788`), aber kein Browser-Client wertet es aus.
`gateway/detail.html` enthaelt kein einziges `<script>`, `hx-get` oder `hx-trigger`. Der
Abbrechen-Flow macht einen Redirect-Reload (`ui_gateway.py:789`), was `CLAUDE.md` untersagt.

**Es braucht keine neue WebSocket-Verbindung.** `/ws/global` (`ws.py:240-259`) ist ueber
`app/static/js/app.js:120-163` (gebunden in `base.html:47`) auf jeder Seite offen.

**Loesung** nach dem kanonischen Muster *WS-Event → `CustomEvent` auf `document.body` → HTMX
`hx-trigger="… from:body"`* (Vorbild `app.js:136-140` + `incident/board.html:243-247`):

1. `app/static/js/app.js`, neben die bestehenden Zweige in `ws.onmessage`:
   ```js
   if (ev.type === 'print_job_status') {
     document.body.dispatchEvent(new CustomEvent('print-job-status', { detail: ev, bubbles: true }));
   }
   ```
2. Panel aus `detail.html:336-380` nach `app/templates/gateway/_druckhistorie.html`. Es traegt
   sein eigenes `hx-get` und ersetzt sich selbst (Muster `admin/_dibos_traces.html:2`):
   ```html
   <div class="gwx-col-12" id="historie"
        hx-get="/gateway/{{ gw.id }}/historie"
        hx-trigger="print-job-status from:body"
        hx-swap="outerHTML">
   ```
   **Eine einzige ID, und zwar `historie`** — der bestehende Redirect-Anker `#historie`
   (`ui_gateway.py:789`) und das HTMX-Target muessen dieselbe sein. Das innere Panel
   (`detail.html:338`) verliert seine ID an den Wrapper.
3. Neue Route `GET /gateway/{gateway_id:int}/historie`, gleiche Guards wie `gateway_detail`.
4. Gemeinsame Kontext-Funktion `_historie_context(db, gw)` in `ui_gateway.py`, genutzt von
   `gateway_detail` (`:89-108`), der neuen Partial-Route **und** dem Abbrechen — damit alle drei
   dieselben gateway-gefilterten `printers`/`jobs` liefern.
5. Abbrechen auf HTMX: Formular mit `hx-post`, `hx-target="#historie"`, `hx-swap="outerHTML"`.
   **Das versteckte `_csrf`-Feld muss im Partial erhalten bleiben** (`detail.html:365-368`); die
   Middleware akzeptiert es als Formfeld (`app/middleware/csrf.py:6-8`). `cancel_print_job`
   liefert bei `HX-Request` das Partial, sonst weiterhin den Redirect (303).

**Org-weites Event, eine Gateway-Seite:** Ein Job an einem anderen Gateway derselben Org loest
eine ueberfluessige Partial-Anfrage aus. Das ist bewusst akzeptiert — die Route filtert danach
gateway-genau (`ui_gateway.py:95-97`), es kostet nur einen leeren Request. Ein Filter auf
`gateway_id` wuerde voraussetzen, dass der Broadcast dieses Feld immer mitsendet, was er heute
nicht tut (`ws.py:670-671`); der neue Broadcast aus Punkt 1 fuehrt es ein, die bestehenden
Sender muessten nachgezogen werden. Nicht in diesem Zug.

---

## Punkt 3 — `reachable` ehrlich machen

**Problem.** `_mark_gateway_offline` (`ws.py:729-742`) setzt beim Disconnect nur
`Gateway.status` und `serial_connected`. Alle Drucker behalten
`{"reachable": true, "checked_at": <alt>}`. Folgen: die Kachel „Drucker online"
(`detail.html:62-75`) zaehlt weiter gruen, die Chips (`:257-274`) zeigen „erreichbar", der
Druckdialog (`_print_dialog.html:141,176`) zeigt gruene Punkte. Und `:274` rendert
`checked_at[11:16]` — **nur HH:MM ohne Datum**, ein Wert von vorgestern sieht aus wie einer von
vor zwei Minuten.

**Loesung: beim Lesen ableiten, nicht beim Disconnect invalidieren.** Die Invalidierung schreibt
viele Zeilen, verwirft den letzten bekannten Wert und deckt den wichtigsten Fall nicht ab —
reisst die Verbindung hart ab, laeuft `_mark_gateway_offline` nie und `Gateway.status` bleibt
fuer immer `online`.

Neuer Helper in `app/services/gateway_service.py`:

```python
def printer_reachable(printer, gateway) -> bool | None:
    """True/False = gemeldeter Stand, None = unbekannt (Gateway weg oder Meldung zu alt).

    Die Frische-Schwelle leitet sich aus dem Health-Intervall DIESES Gateways ab
    (wut_config.health_interval_s, Default 60, Minimum 15, nach oben frei) — ein fester
    Wert wuerde bei langem Intervall gesunde Drucker als unbekannt zeigen.
    """
```

Zwei Punkte, die der erste Entwurf falsch hatte:

- **Gateway-genau, nicht org-weit.** `printers_json` liefert die Drucker **aller** Gateways der
  Org (`ui_gateway.py:659-665`, Filter auf `Printer.org_id`), waehrend `_is_connected`/`connected`
  org-weit ist (`:50-57`). Die Ableitung muss deshalb pro Drucker das **eigene** Gateway
  heranziehen (`Gateway.status == online` **und** frisches `last_seen_at`), nicht die org-weite
  Sammelauskunft.
- **Schwelle aus `health_interval_s`.** Das Intervall ist je Gateway konfigurierbar
  (`ui_gateway.py:229-249`, Minimum 15 s, nach oben offen). Ableitung: dreifaches Intervall mit
  sinnvoller Untergrenze (z. B. `max(180, 3 * health_interval_s)`).

Umstellen auf den Helper: `printers_json` (`:673-674`), Kachel (`detail.html:62-75`), Chips
(`:257-274`). Die abgeleiteten Werte gehoeren im **Route-Kontext** vorbereitet — ein Template,
das weiter direkt auf `p.status` zugreift, liest sonst den alten Wert. `_print_dialog.html`
braucht keine JS-Aenderung: bei `null` zeigt es weder Punkt noch Offline-Hinweis.

Zusaetzlich `checked_at` ehrlich anzeigen: den naiven UTC-ISO-String im Route-Kontext zu einem
`datetime` parsen und per `|local_datetime` rendern statt `[11:16]` (Zeitzonen-Pflicht aus
`CLAUDE.md`). Eingehende Payloads koennen ein eigenes `checked_at` mitbringen
(`printer_report_service.py:36-50`, `setdefault`) — das Parsen muss ungueltige und
timezone-behaftete Werte vertragen.

---

## Punkt 4 — Tote Ausloeser scharfschalten

**Problem, zweifach.** `einsatz_updated` und `gsl_lage_updated` sind in `TRIGGER_LABELS`
(`app/models/gateway.py:138-146`) definiert, im UI als „(noch nicht aktiv)" beschriftet
(`druckregeln.html:62,121`) und tot:

- **(a)** `TRIGGER_DOCUMENT_TYPES` (`:161-166`) hat fuer beide keinen Eintrag. `rule_save`
  (`ui_gateway.py:500-506`) lehnt daher jedes gewaehlte Dokument ab — man kann gar keine Regel
  mit Inhalt speichern.
- **(b)** Niemand ruft `on_event` mit diesen Triggern auf.

**Nachzuegler-Semantik (entschieden).** Der Idempotenzschluessel (`print_dispatcher.py:60-93`)
bleibt **unveraendert**. Genau das macht das Feature richtig: es druckt nur, was noch nicht
gedruckt wurde. Praxisfall ist der im Architekturplan vorgesehene, nie gebaute Nachzuegler
(`docs/plans/ecpg-gateway-plan.md:76,118,133`): Alarm laeuft, Einsatzinfo ist gedruckt, spaeter
wird ein Objekt bestaetigt — dann kommen Objektblatt und Objektunterlagen nach.
`_incident_context` (`:399-415`) und `_resolve_objekt_ids` (`:606-623`) lesen die bestaetigten
Verknuepfungen zum Auswertungszeitpunkt und finden neue Objekte korrekt (von Codex bestaetigt).

### Umsetzung

1. `app/models/gateway.py:161-166` ergaenzen:
   - `TRIGGER_EINSATZ_UPDATED: frozenset({DOC_EINSATZINFO, DOC_OBJEKTBLATT, DOC_OBJEKT_DOKUMENT})`
   - `TRIGGER_GSL_LAGE_UPDATED: frozenset({DOC_GSL_LAGEBLATT, DOC_GSL_BERICHT})`

   Das reicht fuer `rule_save` (`ui_gateway.py:500-506`) und `_jobs_for_rule`
   (`print_dispatcher.py:684-698`) — beide pruefen genau diese Map.
2. `print_dispatcher.py`: `autoprint_incident_background` und `autoprint_gsl_background` um einen
   Trigger-Parameter erweitern (Default = bisheriges Verhalten) statt Kopien anzulegen; duenne
   Wrapper fuer die `add_task`-Aufrufe.
3. **Drei der fuenf Hook-Routen haben kein `BackgroundTasks`-Argument** und muessen es bekommen:
   `alarm_save` (`ui_incident.py:558-565`), `address_save` (`:3388-3398`),
   `lage_bearbeiten_save` (`ui_major_incident.py:1728-1737`).

   | Route | Anlass | Bedingung |
   |---|---|---|
   | `ui_objekt.py:2680-2685` | Objektverknuepfung bestaetigt — der eigentliche Nachzuegler-Punkt | nach dem Commit; die Route hat bereits `background_tasks` |
   | `ui_objekt.py:2623-2648` | Objekt manuell verknuepft | **nur im tatsaechlich neuen Zweig**, nicht bei bereits bestehender Verknuepfung |
   | `ui_incident.py:568-570` | Stichwort geaendert (`alarm_save`) | nach dem Commit |
   | `ui_incident.py:3417-3429` | Adresse geaendert (`address_save`) | nach dem Commit |
   | `ui_major_incident.py:1728-1751` | GSL-Stammdaten geaendert | nach dem Commit |

4. `druckregeln.html:62,121`: „(noch nicht aktiv)" fuer diese beiden Trigger entfernen. Dafuer ein
   `gwx-help`-Hinweis im Regel-Editor: die Regel druckt nur, was noch nicht gedruckt wurde — sonst
   wundert sich ein Admin, warum ein zweites Update nichts ausloest.

### `alarm_serial_received` gleich mit scharfschalten

Er ist ebenfalls als „(noch nicht aktiv)" beschriftet, hat aber im Gegensatz zu den anderen
bereits einen `TRIGGER_DOCUMENT_TYPES`-Eintrag. Es fehlt **beides**: eine Background-Funktion
und der Aufruf.

- Neu `autoprint_alarm_background(alarm_ingest_id)` in `print_dispatcher.py`, Kontext
  `{"alarm_ingest_id": …, "incident_id": ingest.einsatz_id}`. Der Alarm-Zweig in `_jobs_for_rule`
  (`:701-717`) existiert seit #283 und setzt `artifact_ref` auf die Ingest-ID — jeder Alarm druckt
  damit genau einmal.
- Aufruf in `gateway_api.py:133-135`, wo heute nur `autoprint_incident_background` fuer
  `einsatz_created` steht.
- **Bedingung praezise:** fuer **jeden neuen Ingest** (`created=True`), unabhaengig von
  `dedup_action` — ein auf einen bestehenden Einsatz gemergter Alarm hat trotzdem einen Rohtext,
  der gedruckt gehoert. **Nie** bei `created=False`, denn das ist ein Retry desselben `raw_hash`
  (`serial_alarm_service.py:38-50`).
- Der tatsaechliche Wert ist `"merged"` bzw. `"created"` (`serial_alarm_service.py:106,129`) —
  der Modellkommentar `# created / merged_lis / merged_api` (`gateway.py:350`) ist veraltet und
  wird bei der Gelegenheit korrigiert.

---

## Reihenfolge

1. Punkt 3 (`reachable`) — unabhaengig, reine Anzeige.
2. Punkt 2 (Historie live) — unabhaengig; liefert die Sichtbarkeit fuer Punkt 1.
3. Punkt 1 (Eskalation) — **nach** Punkt 2, weil der Eskalations-Broadcast auf den dort
   gebauten Client-Zweig trifft.
4. Punkt 4 (Trigger) — groesster Block, unabhaengig von 1-3.

---

## Tests

`.venv/bin/python -m pytest -q` (kein `python` im PATH), ~4 min, aktuell 2334 passed / 1 skipped.
**Nie zwei Laeufe parallel** — gemeinsame SQLite-Datei, sonst hunderte falsche Fehler.

- **Watchdog** (heute **null** Tests): juenger als 30 min → nur Warnung; aelter → `failed` mit
  Begruendung; `done`/`canceled` → unangetastet; **echtes Rennen**: zwei parallele
  `escalate_stale_job`-Aufrufe auf denselben Job → genau einer bekommt `True`; Eskalation loest
  Broadcast **und** `dispatch_fallback_for_failed_job` aus (beide gemonkeypatcht); zweiter Lauf
  eskaliert nicht erneut.
- **Historie live**: `GET /gateway/{id}/historie` → 200, enthaelt Tabelle und eigenes `hx-get`;
  Guards (403 `recorder`, 404 ohne Modul); Abbrechen mit `HX-Request` liefert das Partial, ohne
  Header weiterhin 303; **`_csrf` ist im Partial vorhanden** und ein HTMX-Cancel mit gueltigem
  Token geht durch; Event eines fremden Gateways laedt zwar nach, liefert aber nur die Jobs des
  angezeigten Gateways.
- **`reachable`**: Gateway verbunden + frisch → gemeldeter Wert; Gateway getrennt → `None`;
  `checked_at` aelter als abgeleitete Schwelle → `None`; fehlend/ungueltig/mit Zeitzone → `None`
  statt Absturz; **zwei Gateways derselben Org**, eines verbunden, eines nicht → die Drucker des
  getrennten liefern `None`, die des verbundenen den echten Wert; Gateway mit
  `health_interval_s = 300` → ein 200 s alter Stand gilt weiterhin als frisch.
- **Trigger** (fuer `autoprint_incident_background`/`autoprint_gsl_background` gibt es heute
  **keinen** Test): Regel `einsatz_updated` + `objektblatt` erzeugt nach nachtraeglicher
  Objektbestaetigung einen Job; **Negativtests**: zweite Bestaetigung, unveraendertes Speichern
  und eine bereits bestehende manuelle Verknuepfung erzeugen **keine** weiteren Jobs;
  `rule_save` akzeptiert jetzt Dokumente fuer beide Trigger.
- **Alarm-Trigger**: `created=True, dedup_action="created"` → ein Job; `created=True,
  dedup_action="merged"` → ebenfalls ein Job; `created=False` (Raw-Hash-Retry) → **kein** Job;
  Rohtext-Rendering ueber `artifact_ref`.
- `tests/test_gateway_rule_test_route.py:62,65` parametrisiert beide Trigger bereits fuer den
  Testdruck und muss gruen bleiben.

## Verifikation

1. Voller Lauf gruen.
2. Historie: Statuswechsel ueber den Gateway-WS simulieren → Tabelle aktualisiert sich ohne
   Reload; Abbrechen erzeugt keinen Seitenreload mehr.
3. `reachable`: Gateway-WS trennen → Kachel und Chips wechseln auf „unbekannt",
   `printers.json` liefert `null`, der Druckdialog zeigt keine gruenen Punkte.
4. Eskalation: Job mit `aktualisiert_am` 40 min in der Vergangenheit, Watchdog-Iteration
   ausloesen → Job `failed`, Ersatzdruck auf dem Fallback-Drucker, beides **live** in der Historie.
5. Nachzuegler: Regel `einsatz_updated` + Objektblatt, Einsatz ohne Objekt anlegen (nichts
   gedruckt), dann Objekt bestaetigen → Objektblatt kommt nach; erneutes Bestaetigen druckt nicht.

## Ausserhalb des Scopes

- **`Gateway.offline_alerted_at` und `OrgSettings.gateway_offline_alert_min`** (Default 15, beide
  seit Migration 0141) werden weiterhin nirgends gelesen. Eine Admin-Benachrichtigung braucht
  einen Benachrichtigungsweg (Mail/Teams/Push) und ist ein eigener Auftrag. Ebenso bleibt
  unabgestimmt, dass `gateway_online()` (`ws.py:788`) mit hartkodierten 2 Minuten arbeitet,
  waehrend das Org-Feld 15 vorsieht.
- **Kein Heartbeat-Timeout-Job**: reisst die WS-Verbindung hart ab, bleibt `Gateway.status`
  dauerhaft `online`. Punkt 3 entschaerft die Folge fuer die Druckeranzeige, behebt aber nicht
  die Ursache.
- **`gateway_id` in allen `print_job_status`-Broadcasts**: Punkt 1 fuehrt das Feld ein, die
  bestehenden Sender (`ws.py:670-671`, `ui_gateway.py:788`) ziehen es nicht nach. Erst danach
  liesse sich das Partial-Reload auf das eigene Gateway filtern.
- **`manual_only`** bekommt weiterhin keine Dokumenttypen — eine Produktentscheidung, die nicht
  Teil des Auftrags war.
- **Neudruck bei jeder Aenderung** ist bewusst nicht implementiert. Wer ein periodisch
  aktualisiertes Lageblatt braucht, braucht einen zeitgesteuerten Ausloeser — ein eigenes Feature.
