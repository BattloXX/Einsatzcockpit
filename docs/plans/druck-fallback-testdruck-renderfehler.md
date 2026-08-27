# Plan: Druck-Vorbefunde beheben (Fallback-Drucker, Testdruck, Renderfehler)

Repo `/home/johannes/projects/Einsatzcockpit`, Basis `main` @ `61ed438`.

## Context

Beim Umbau der Druckregeln in ein eigenes Tab (PR #282) sind zwei Altlasten aufgefallen, die
bewusst nicht mitbehoben wurden. Die Recherche dazu hat einen dritten, gravierenderen Befund
freigelegt.

1. **`PrintRule.fallback_printer_id` ist ein totes Feld.** Es steht im Regel-Editor
   („Fallback-Drucker (wenn Ziel offline)", `app/templates/gateway/druckregeln.html:155-162`),
   wird gespeichert (`ui_gateway.py:465,509`) — und nirgends gelesen. Laut
   `docs/plans/ecpg-gateway-plan.md:184` und PR5 (`:226`) sollte der Fallback im
   Gateway-Container ausgeführt werden; das Dispatch-Payload
   (`print_dispatcher.py:152-158`) enthält `fallback_printer_id` aber gar nicht. Die
   Funktion wurde also nie fertiggebaut. Der Gateway-Container liegt nicht in diesem Repo,
   deshalb zieht dieser Plan den Fallback bewusst in die Cloud.

2. **Der Testdruck ist für fast alle Auslöser kaputt.** `rule_test`
   (`ui_gateway.py:564-603`) lädt immer den letzten `Incident`. Folge: `gsl_created` erzeugt
   Jobs mit `gsl_id=None` (Renderer wirft später `ArtifactError`), `verleih_created` erzeugt
   0 Jobs, `alarm_serial_received` druckt ein leeres Blatt.

3. **Renderfehler sind unsichtbar.** `render_job_pdf` wirft `ArtifactError`; der
   Artefakt-Abruf (`gateway_api.py:147-164`) macht daraus HTTP 422 für das Gateway. Der Job
   bleibt in der Cloud auf `sent` und sieht in der Historie wie ein Erfolg aus. Deshalb
   melden die kaputten Testdrucke aus (2) fröhlich „an die Zieldrucker gesendet".

**Ergebnis:** Fallback-Drucker funktioniert, Testdruck funktioniert für jeden Auslöser mit
ehrlicher Rückmeldung, und fehlgeschlagene Drucke sind in der Historie als solche erkennbar.

## Tragende Entwurfsentscheidungen

**Der Statusübergang wird zum zentralen Gate.** `_apply_job_status`
(`app/routers/ws.py:548-564`) schreibt heute jeden gemeldeten Status blind — auch Rückschritte
und auch auf bereits abgeschlossene Jobs. Es bekommt eine Status-Whitelist und einen
Terminal-Schutz und gibt `org_id` künftig nur zurück, wenn der Übergang wirklich vollzogen
wurde. Daran hängen alle drei Features:

- Ein zweites `failed` vom Gateway wird ignoriert → kein zweiter Fallback.
- Ein manuell abgebrochener Job (`canceled`, `ui_gateway.py:772`) kann nicht mehr auf `failed`
  zurückgedreht werden → kein Fallback für abgebrochene Jobs.
- Ein Job, den ein Renderfehler schon auf `failed` gesetzt hat, macht den späteren
  `failed`-Callback zum No-Op → **der Fallback unterbleibt automatisch**. Ein Renderfehler ist
  kein Druckerproblem; der Ersatzdruck würde denselben Fehler erzeugen. Das fällt hier aus der
  Architektur, statt ein Sonderfall-`if` zu sein.

**Der Fallback lebt in `print_dispatcher`, nicht in `ws.py`.** `_apply_job_status` schließt
seine Session im `finally`, `dispatch_job` ist async und braucht eine lebende Session. `ws.py`
bleibt Transportschicht und bekommt nur eine Aufrufstelle. Muster: die bestehenden
`autoprint_*_background`-Funktionen (`print_dispatcher.py:330,360,387`).

**Der Fallback ist ein Sicherheitsnetz, kein sofortiges Umleiten.** Das Gateway meldet `failed`
laut `docs/plans/ecpg-gateway-plan.md:171` erst nach eigenem Retry mit Backoff
(Default 5×/10 min). Der Ersatzdruck läuft also rund zehn Minuten nach dem Original an. Das ist
korrekt so — es verhindert Doppeldrucke bei kurzen Störungen —, sollte aber im Hilfetext des
Feldes stehen, damit niemand ein Sofort-Umschalten erwartet.

**Neue Spalte `PrintJob.fallback_of_job_id`.** Der Idempotenzschlüssel allein reicht nicht: er
beantwortet nicht „ist dieser Job selbst ein Ersatz?" (Ketten-Fallback), er bricht, wenn ein
Admin die Regel zwischen Zustellung und Fehlschlag editiert, und wenn der Fallback-Drucker
auch in `printer_ids` steht, würde `create_print_job` still den bestehenden Job liefern und der
Ersatzdruck wäre verschluckt. Bewusst **ohne** FK-Constraint: reine Herkunftsmarkierung, wird
nur gegen `None` geprüft und angezeigt; ein selbstreferenzierender FK zwingt
`batch_alter_table` auf SQLite in den Table-Recreate-Pfad.

## Schritt 1 — Modell + Migration

`app/models/gateway.py`, in `PrintJob` nach `error`:

```python
    # Herkunft eines Ersatz-Drucks: id des fehlgeschlagenen Original-Jobs. Bewusst ohne
    # FK (reine Markierung). Gesetzt => dieser Job IST ein Fallback und loest selbst
    # nie wieder einen aus.
    fallback_of_job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

Neu `alembic/versions/0227_print_job_fallback_of.py`, `down_revision = "0226"`. Reine DDL, kein
Backfill. Wiederaufsetzbar über `sa.inspect(op.get_bind()).get_columns("print_job")` statt
`IF EXISTS`, Spalte per `op.batch_alter_table` — Muster
`alembic/versions/0226_verleih_autodruck_zu_druckregel.py`. Kein Reserved-Word-Quoting nötig
(`print_job` hat keins).

## Schritt 2 — `_apply_job_status` härten

`app/routers/ws.py:548-564`:

```python
def _apply_job_status(job_id: int, status: str, error: str | None) -> int | None:
    """Schreibt job_status vom Gateway in die DB.

    Gibt org_id zurueck, wenn der Statusuebergang tatsaechlich vollzogen wurde, sonst
    None (unbekannter Status, Job weg, oder Job bereits in einem Terminal-Status).
    """
```

Ablauf: `status not in JOB_STATUS_LABELS` → warnen, `None`. `job is None or job.status in
JOB_TERMINAL` → `None`. Sonst wie bisher schreiben und `job.org_id` zurückgeben.
`JOB_TERMINAL` existiert bereits (`app/models/gateway.py:124`). Der Aufrufer
(`ws.py:656-663`) behält sein `if o:` unverändert.

Verhaltensänderung, die in den Commit-Text gehört: Statusmeldungen nach Erreichen eines
Terminalzustands werden künftig still verworfen (nur Log).

## Schritt 3 — Renderfehler sichtbar machen

Neu in `app/services/print_dispatcher.py` (nach `dispatch_job`, ~Zeile 191):

```python
def mark_job_failed(db: Session, job: PrintJob, grund: str) -> bool:
    """Setzt einen Job auf 'failed' mit Begruendung. Idempotent und terminal-sicher.
    True, wenn der Status gewechselt hat."""
```

Einbau in `app/routers/gateway_api.py` im `except ArtifactError` von **beiden** Routen —
`get_artifact` (`:147-164`) und `get_render_page` (`:167-193`, dort liegt genauso ein echter
`PrintJob` vor):

```python
    except ArtifactError as exc:
        mark_job_failed(db, job, f"Rendern fehlgeschlagen: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
```

**Kein `broadcast_org` hier.** Clientseitig hört niemand auf `print_job_status` (kein Listener
in Templates/JS), die Historie lädt ohnehin erst beim Reload. Beide Routen sind `def` im
Threadpool — ein `await` steht nicht zur Verfügung, und die Route auf `async def` umzustellen
würde WeasyPrint auf den Event-Loop legen. Als Kommentar vermerken, nicht bauen.

`app/routers/ui_druck.py:176-177` bleibt funktional unverändert: dort ist der „Job" ein
`SimpleNamespace` (`:166-174`), es gibt nichts zu persistieren, und `_fehler_seite` zeigt den
Grund bereits. Nur ein fehlendes `logger.warning` im `ArtifactError`-Zweig ergänzen.

## Schritt 4 — Fallback-Drucker

`build_idempotency_key` (`print_dispatcher.py:44-74`) bekommt `fallback_of: int | None = None`.
Das Segment `f"fb{fallback_of}"` wird **nur bei gesetztem Wert** angehängt — würde es
unconditional angehängt, änderten sich alle bestehenden Hashes und die Dedup gegen Altbestand
bräche einmalig auf (Doppeldruckrisiko). Das ist die wichtigste Fallgrube dieses Schritts und
wird per eingefrorenem Erwartungswert im Test abgesichert.

`create_print_job` reicht `fallback_of_job_id` durch und setzt es am Job.

Neu in `print_dispatcher.py`:

```python
async def dispatch_fallback_for_failed_job(job_id: int) -> PrintJob | None:
    """Legt nach einem gemeldeten Fehlschlag genau einen Ersatz-Job auf dem
    Fallback-Drucker der Regel an und stellt ihn zu. Eigene Session, best-effort."""
```

Abbruchbedingungen der Reihe nach: Job weg · `source != rule` · `rule_id is None` ·
`job.fallback_of_job_id is not None` (kein Ketten-Fallback) · Regel weg oder fremde Org ·
`not rule.fallback_printer_id` · `rule.fallback_printer_id == job.printer_id` · Drucker weg,
fremde Org oder inaktiv (warnen statt crashen, Muster `ui_gateway.py:700-702`).

Sonst: Optionen kopieren und `media` verwerfen, wenn der Fallback-Drucker das Format laut
`capabilities` nicht kann (Muster `ui_gateway.py:705-708`) — sonst scheitert der Ersatzdruck
aus demselben Grund. Dann `create_print_job(...)` mit `gateway_id=printer.gateway_id`
(nicht `job.gateway_id` — der Fallback kann an einem anderen Gateway derselben Org hängen),
`fallback_of_job_id=job.id`. Bei `created=False` abbrechen, sonst committen, `logger.info` und
`await dispatch_job(db, ersatz)`.

Aufrufstelle `app/routers/ws.py:656-663`, innerhalb des bestehenden `if o:`:

```python
                        if p.get("status") == JOB_FAILED:
                            await dispatch_fallback_for_failed_job(int(jid))
```

Damit hängt der Fallback am tatsächlich vollzogenen Übergang. Drei unabhängige
Exactly-once-Sicherungen: Terminal-Schutz, `fallback_of_job_id`, deterministischer Schlüssel.

**Nicht abgedeckt:** der `RuntimeError`-Pfad in `dispatch_job` (`:180-187`, „kein Gateway
verbunden"). Ist gar kein Gateway da, ist der Fallback-Drucker derselben Org genauso
unerreichbar. Als Kommentar dort vermerken.

**Entschieden:** Bei `objekt_dokument` erzeugt eine Objektunterlage N Seiten-Jobs
(`_jobs_for_rule:551-557`). Fällt der Drucker aus, entsteht pro Job genau ein Ersatz, in Summe
also der vollständige Satz auf dem Fallback-Drucker. Das ist gewollt — alles andere ließe
Seiten fehlen.

**Historie** (`app/templates/gateway/detail.html:355`, Spalte „Dokument"): hinter
`{{ j.document_label }}` ein `{% if j.fallback_of_job_id %}<span class="gwx-chip">Ersatz für
#{{ j.fallback_of_job_id }}</span>{% endif %}`. Kein Route-Change nötig.

**Hilfetext** im Regel-Editor (`druckregeln.html:155-162`) ergänzen: greift erst, wenn das
Gateway den Auftrag endgültig aufgibt (nach dessen eigenem Retry), nicht sofort.

## Schritt 5 — Testdruck für alle Auslöser

Die Bezugsauflösung wird eine eigene Funktion, `build_test_jobs` nimmt künftig den fertigen
Kontext. Grund: eine Funktion, die intern auflöst und `[]` zurückgibt, könnte „kein
Bezugsobjekt" nicht von „0 Aufträge entstanden" unterscheiden — genau die Unterscheidung, die
die UI braucht.

Neu in `print_dispatcher.py`:

```python
class TestBezug(NamedTuple):
    context: dict
    art: str       # 'einsatz' | 'gsl' | 'verleih' | 'alarm'
    ref_id: int

TRIGGER_TEST_BEZUG = {...}   # Ausloeser -> Bezugsart

def resolve_test_context(db: Session, rule) -> TestBezug | None: ...
def paired_gateway(db: Session, org_id: int) -> Gateway | None: ...
```

Auflösung je Art, jeweils jüngster Datensatz der Org:

| Art | Quelle | Kontext |
|---|---|---|
| `einsatz` (`einsatz_created`, `einsatz_updated`, `manual_only`) | `Incident.primary_org_id`, `started_at desc` | `_incident_context` (`:287`) |
| `gsl` (`gsl_created`, `gsl_lage_updated`) | `MajorIncident`, `started_at desc` | `_gsl_context` (`:321`) |
| `verleih` (`verleih_created`) | `VerleihAusleihe`, `created_at desc`; Lage per `db.get(MajorIncident, a.lage_id)` | `{gsl_id, ausleihe_id, is_exercise}` wie `:409-413` |
| `alarm` (`alarm_serial_received`) | `AlarmIngest`, `received_at desc` (Index `(org_id, received_at)` vorhanden) | `{alarm_ingest_id, incident_id: ing.einsatz_id}` |

**`MajorIncident` erbt von `Base`, nicht `TenantScoped`** (`app/models/major_incident.py:183`)
— der `org_id`-Filter ist zwingend, sonst druckt eine Org fremde Lagedaten.

`paired_gateway` ersetzt nebenbei die dreifach duplizierte Gateway-Suche (`on_event:205-210`,
`build_test_jobs:568-573`, sinngemäß in den `autoprint_*`).

`_jobs_for_rule` (`:482-558`) bekommt in Schleife 1 einen Zweig vor dem generischen `_add`:
bei `DOC_ALARM_ROHTEXT` wird `artifact_ref=str(context["alarm_ingest_id"])` gesetzt, sonst
rendert `_render_alarm_rohtext` (`print_artifact_service.py:183-198`) ein leeres Blatt.
Bewusst ein dedizierter Kontext-Schlüssel statt eines generischen `context["artifact_ref"]` —
letzteres würde später in `einsatzinfo`/`gsl_lageblatt`-Jobs sickern und dort den
Idempotenzschlüssel verändern. Der Zweig repariert nebenbei den echten Automatikpfad, falls
`alarm_serial_received` einmal scharfgeschaltet wird.

`rule_test` (`ui_gateway.py:564-603`): Reihenfolge `rule/404` → `printer_ids` → `paired_gateway`
(`test_err=gateway`) → `resolve_test_context` (`test_err=<art>`) → `build_test_jobs` → bei
leerem Ergebnis `test_err=leer`, sonst `test_ok=N&test_art=<art>&test_ref=<id>`. Fehlercode ist
die Bezugsart, damit Route und Template nicht zwei Vokabulare pflegen; das heutige
`test_err=incident` wird zu `test_err=einsatz`.

`app/templates/gateway/druckregeln.html:42-45`: Meldungsblock je Variante — `printer`,
`gateway`, `einsatz`, `gsl`, `verleih`, `alarm`, `leer`. Bei `leer` konkret werden („Für
Einsatz #123 entstanden keine Druckaufträge – prüfen Sie Dokumente und Objekt-Elemente der
Regel, z. B. Objektblatt ohne verknüpftes Objekt."). Erfolgsmeldung nennt Bezugsart und ID
statt nur `Einsatz #`.
**CLAUDE.md:** nur gerade ASCII-Anführungszeichen in Attributen; typografische nur im
sichtbaren Text.

## Reihenfolge

1. Modell + Migration 0227 (kein Verhalten)
2. `_apply_job_status` härten
3. `mark_job_failed` + `gateway_api` beide Routen
4. Idempotenz-Kwarg + `dispatch_fallback_for_failed_job` + `ws.py`-Hook + Historie-Chip
5. `paired_gateway`, `resolve_test_context`, `build_test_jobs`-Signatur, Alarm-Zweig,
   `rule_test`, Template

2 vor 4 ist zwingend (sonst fehlt dem Fallback die Exactly-once-Sicherung), 3 vor 4 stark
empfohlen (sonst löst jeder Renderfehler in der Zwischenzeit einen sinnlosen Ersatzdruck aus).

## Tests

`.venv/bin/python -m pytest -q` — **nie zwei Läufe parallel** (gemeinsame SQLite-Datei), ein
voller Lauf dauert ~4 min.

- **Migration** — neu `tests/test_migration_0227_print_job_fallback.py` (Muster
  `test_migration_0226_*`): Upgrade legt die Spalte an, zweiter Lauf No-Op, Downgrade entfernt
  sie, zweiter Downgrade No-Op.
- **`_apply_job_status`** — in `tests/test_gateway_dispatch.py`: `sent→printing` wird
  angewandt; `done→printing`, `canceled→failed` und ein unbekannter Status werden ignoriert
  (`None`, Status unverändert); Job weg → `None`.
- **Idempotenz** — in `tests/test_gateway_pr1.py`: ohne `fallback_of` **derselbe** Hash wie
  bisher (Erwartungswert als Literal einfrieren, Drift-Regression); mit `fallback_of` ein
  anderer, aber deterministisch.
- **Fallback** — neu `tests/test_print_fallback.py` (`dispatch_job` gemonkeypatched): Happy
  Path (genau ein Ersatz, richtiger Drucker, `fallback_of_job_id` gesetzt); zweiter
  `failed`-Callback → kein zweiter; Ersatz schlägt fehl → keine Kette; `source=manual` /
  `rule_id=None` / kein Fallback-Drucker / Fallback == Ziel → jeweils `None`; Drucker inaktiv
  oder fremde Org → `None` + Warn-Log; A3-Option wird verworfen, wenn der Ersatzdrucker kein
  A3 kann; abgebrochener Job + `failed` → kein Fallback (über den WS-Pfad).
- **Renderfehler** — neu `tests/test_print_render_failure.py`: Artefakt-Abruf für einen
  `gsl_lageblatt`-Job ohne `gsl_id` → 422 **und** `job.status == "failed"` mit Grund in
  `job.error`; zweiter Abruf idempotent; `canceled` bleibt `canceled`; analog für die
  Render-Route; Integration: nach dem 422 meldet das Gateway `failed` → **kein** Fallback-Job.
- **Testdruck** — `test_build_test_jobs_ignores_trigger_and_filters`
  (`tests/test_gateway_pr1.py:518`) auf die neue Signatur ziehen; `resolve_test_context` je
  Auslöser liefert richtige Art/ID; Org-Isolation besonders für `MajorIncident`;
  `gsl_created` → Job mit `gsl_id != None`; `verleih_created` → `gsl_id == a.lage_id` **und**
  `artifact_ref == str(a.id)`; `alarm_serial_received` → `artifact_ref == str(ingest.id)`.
- **Route** — neu `tests/test_gateway_rule_test_route.py` (Login-Muster aus
  `tests/test_gateway_druckregeln_render.py:18-24`): erste Tests überhaupt für
  `/gateway/rules/{id}/test`. Je Auslöser mit und ohne Bezugsobjekt; ohne Drucker; ohne
  gekoppeltes Gateway; Objektblatt-Regel ohne Objektbezug → `test_err=leer`.
- **Template** — `tests/test_gateway_druckregeln_render.py` um einen Smoke je `test_err`-Variante
  ergänzen.

## Verifikation

1. Voller Lauf `.venv/bin/python -m pytest -q` grün (aktuell 2297 passed, 1 skipped).
2. `alembic upgrade head` und `alembic downgrade -1` gegen eine Wegwerf-DB.
3. Manuell im laufenden Server: Regel mit Auslöser „Verleihschein angelegt" anlegen,
   Testdruck auslösen → Meldung nennt „Verleihschein #<id>", Job mit gesetztem `gsl_id` und
   `artifact_ref` in der Historie. Dasselbe für eine GSL-Regel.
4. Renderfehler erzwingen: GSL-Regel-Job mit geleertem `gsl_id`, Artefakt abrufen → 422, Job
   steht in der Historie auf „Fehlgeschlagen" mit Grund.
5. Fallback: Regel mit Ziel- und Fallback-Drucker, für den Ziel-Job manuell ein
   `job_status: failed` über den Gateway-WS schicken → genau ein Ersatz-Job auf dem
   Fallback-Drucker, in der Historie als „Ersatz für #N" markiert. Zweites `failed` → kein
   weiterer.

## Bewusst außerhalb des Scopes

- **`Printer.status["reachable"]` veraltet still**, wenn das Gateway offline geht
  (`_mark_gateway_offline`, `ws.py:714-727`, rührt den Druckerstatus nicht an). Betrifft nur
  die Anzeige, weil kein Server-Code Entscheidungen daran knüpft — nach diesem Plan auch
  weiterhin nicht.
- **`timeout_waiting_status`** (`ws.py:878-880`): der Job bleibt auf `sent`, es kommt nie ein
  `failed`, also auch kein Fallback. Der `print_watchdog` loggt das nur. Das ist die Lücke,
  durch die „Drucker offline" am ehesten unbemerkt bleibt — eigener Auftrag.
- **`manual_only`, `einsatz_updated`, `gsl_lage_updated` haben keinen Eintrag in
  `TRIGGER_DOCUMENT_TYPES`** (`app/models/gateway.py:162-167`), können also gar keine
  Dokumente tragen; `rule_save` (`ui_gateway.py:500-506`) lehnt sie ab, die UI beschriftet sie
  mit „(noch nicht aktiv)". Ein Testdruck endet für sie korrekt bei `test_err=leer`. Ob
  „Nur manuell"-Regeln Inhalte tragen sollen, ist eine eigene Produktentscheidung.
- **Live-Aktualisierung der Druckhistorie**: es gibt keinen Client-Listener für
  `print_job_status`. Erst Listener bauen, dann Sender ergänzen.
