# Planspiele & Simulation (Entwicklung)

← [Zurück zur Startseite](Home) · Anwender: [Planspiele & Simulation](Anwender-Planspiele) · Administration: [Planspiele einrichten](Administration-Planspiele)

Ausführlicher Plan mit Architekturentscheidungen und Umsetzungsstand je Phase: `docs/plans/planspiel-generator-plan.md` (§16).

## Grundsatz

Das Planspiel baut **keine zweite Einsatz-, GSL-, Journal- oder Ressourcenlogik**. Es ruft die vorhandenen Services auf:

| Zweck | Service |
|-------|---------|
| Einsatz | `create_incident(is_exercise=True)` |
| GSL | `create_lage` + `lage_einrichten(benachrichtigen=False)` |
| Einsatzstelle | `stelle_simuliert_anlegen` (`source="simulation"`, ohne Geocoding und KI-Prio) |
| Meldungen | `add_message`, `add_comm_entry`, `add_site_log`, `cross_marker_service` |
| Einheiten | `lege_einheit_an`, `dispatch_to_site`, `setze_einheit_status` |

Phasenwechsel von Einsatzstellen entstehen ausschließlich in `gsl_phase_automatik`. Die Engine **liest** den Zustand (Polling) und hängt sich nicht per Hook ein.

## Module

| Datei | Inhalt |
|-------|--------|
| `app/models/planspiel.py` | Szenario/Version/Ereignis, OrtTypZuordnung, Adresse, Run, RunEreignis, Ziel, Mitglied, Beobachtung |
| `app/data/planspiel_katalog.py` | Systemkatalog: Bausteine, 56 Szenarien + F30, Alarmtexte, GSL-Wellen und Schweregewichte (`SYSTEM_KATALOG_VERSION`) |
| `app/data/lfv_stichworte_v03.py` | LFV-Stichworte V03 (read-only, Quelle/Revision) |
| `planspiel_katalog_service.py` | idempotenter Import, Versionen, Update ohne Überschreiben, Duplizieren, JSON-Import/-Export (pydantic, Sandbox-Templates) |
| `planspiel_ort_service.py` | Gebiet (Polygon/Radius), Pool-Aufbau (Objekte, Einsatzhistorie, Overpass), Ortswahl nach Ortstyp/Qualität |
| `planspiel_run_service.py` | Entwurf, Snapshot, Zeitplan (`zeitplan_fuer`), Vorschau, Start |
| `planspiel_engine.py` | Übungsuhr, Steuerung, Regie-Aktionen, Trigger, Claim/Ausführung, GSL-Generator, `tick()` |
| `planspiel_loop.py` | Leader-Loop (`PLANSPIEL_LOOP_INTERVAL_S`), Run-Lease |
| `planspiel_zugriff.py` | Rechte für Einheiten-Simulation (`simulation_erlaubt`) |
| `planspiel_auswertung_service.py` | Kennzahlen aus vorhandenen Zeitstempeln |
| `routers/ui_planspiel.py`, `ui_planspiel_admin.py` | `/planspiele…`, `/admin/settings/planspiele…` |

## Sicherheit: Übungs-Guard

`exercise_guard.darf_extern(kanal, *, is_exercise, org_id, db, planspiel_run_id)`: Der Parameter `planspiel_run_id` ist **Pflicht**. Ist er gesetzt, gelten ausschließlich die `kanal_freigaben` des Runs (Standard `{}` = alles gesperrt); die Org-Übungsflags werden ignoriert.

- `tests/test_planspiel_guard.py` prüft per AST, dass jeder `darf_extern(`-Aufruf `planspiel_run_id=` übergibt. Neue Kanäle fallen so sofort auf.
- `tests/test_planspiel_side_effects.py` lässt einen Einzel- und einen GSL-Run mit **allen** Org-Übungsflags auf `True` laufen und erwartet null Aufrufe von SMS, Teams, Mail, Push, Druck und Nominatim.
- Die Alarm-Outbox kennt den Unterdrückungsgrund „Planspiel“; `task_reminder` und `gsl_live_notify` sind ebenfalls gesperrt.
- Übungssperren sind `CrossSiteMarker(marker_type="strasse_gesperrt", source="simulation")`, nie `RoadClosure`, weil `create_closure` Teams-Meldungen ohne Übungsprüfung verschickt.

## Übungsuhr und Determinismus

```
sim_s = (jetzt − gestartet_at − pausiert_summe_s) × tempo
```

- Ereignisse speichern `faellig_sim_s`. Eine Pause friert die Uhr ein, ein Server-Neustart ist unkritisch.
- Fälligkeiten werden auf ganze Sekunden abgerundet, weil MariaDB-`FLOAT` nur einfach genau ist.
- Zufall: `random.Random(f"{seed}:{zweck}:{seq}")` je Ziehung. Jede GSL-Stelle würfelt ihre Folgemeldungen mit eigenem Seed `"{seed}:stelle:{seq}"`.
- Katalog und Pool werden beim Start als Snapshot im Run eingefroren. Gleicher Seed und gleicher Snapshot ergeben dieselbe Abfolge.

## Worker: Lease, Claim, Recheck

```
planspiel_loop (Leader, alle 2 s)
  claim_run:   UPDATE planspiel_run SET worker_lease_until, worker_id
               WHERE status IN (laeuft, generierung_gestoppt) AND Lease frei/eigen
  tick(run):
    1. reaktive Trigger (phase_reached, assignment_created/completed) gegen Ist-Zustand prüfen
    2. fällige Ereignisse (max. 5): bedingtes UPDATE geplant→laeuft,
       in einer Transaktion steuer_version/Status des Runs neu lesen (FOR UPDATE),
       ausführen, status=ausgefuehrt, commit; danach Broadcasts
    3. Generator (art=gsl): Unique (run_id, seq) auf planspiel_ziel gegen parallele Worker
    4. Rückstand > 120 s Übungszeit: verpasste Ereignisse/Stellen überspringen, nicht nachschießen
```

Pause, Stopp und Beenden erhöhen `steuer_version`. Ein Ereignis, das parallel zu einer Steuerung beansprucht wurde, wird zurückgerollt. Der Leader-Lock ist fail-open; die eigentliche Absicherung sind Lease, Claim und Unique-Schlüssel (`test_planspiel_loop.py` mit `threading.Barrier`).

## Zustände

```
entwurf → laeuft ⇄ pausiert
           laeuft ⇄ generierung_gestoppt (nur GSL)
           * → beendet
```

`beenden()` setzt offene Ereignisse auf `abgebrochen`, widerruft `LageEinheitZugang` der Lage und leert `public_token`/`expires_at`.

## Verknüpfung mit Bestandsdaten

- `incident.planspiel_run_id`, `major_incident.planspiel_run_id` (nullable FK, `SET NULL`)
- GSL-Stellen und Marker: `source="simulation"` + `planspiel_ziel` (`site_id`/`marker_id`, `ist_duplikat_von`, `adress_snapshot`)
- Audit: `planspiel.run.*` (Regie-Aktionen, `entity_type="planspiel_run"`), `planspiel.sim.einheit_angelegt`; die Auswertung liest außerdem `major_incident.site.phase_changed` (mit `org_id`).
- Live-Aktualisierung: `broadcast_org({"type": "planspiel_changed", "run_id"})`, das Cockpit lädt daraufhin gezielt das Timeline-Partial nach.

**Achtung MariaDB:** Fremdschlüssel auf `major_incident`, `incident_site` und `cross_site_marker` müssen `INT` sein (nicht `BIGINT`), sonst errno 150. Im Downgrade den FK vor dem Index droppen.

## Tests

`tests/test_planspiel_*.py`, u. a.:

| Datei | Schwerpunkt |
|-------|-------------|
| `test_planspiel_guard.py`, `test_planspiel_side_effects.py` | keine Außenwirkung |
| `test_planspiel_katalog_*.py` | Datensatz-Vollständigkeit, Import/Update/Versionen, Sandbox |
| `test_planspiel_ort.py` | Gebiet, Qualität, Ortstyp-Regeln, leerer Pool |
| `test_planspiel_engine.py`, `test_planspiel_loop.py` | Uhr, Pause, Trigger, Concurrency, Neustart |
| `test_planspiel_gsl*.py` | Generator, Verteilung/Determinismus, Grenzen, Ausführung |
| `test_planspiel_solo.py` | Solo-Durchlauf über die Phasenautomatik, Rechte, Widerruf |
| `test_planspiel_auswertung.py` | Kennzahlen, PDF, Wiederholen, Statistik-Ausschluss |

Tests mit DB-weiten Abfragen sind unter xdist instabil, daher immer auf eigene IDs bzw. die eigene `org_id` filtern.
