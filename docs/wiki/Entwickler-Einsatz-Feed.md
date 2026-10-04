# Einsatz-Feed-Schema

← [Zurück zur Startseite](Home)

Betrieb, Scopes und Sicherheit: [Einsatz-Feed](Administration-Einsatz-Feed). Code: `app/routers/api_feed.py`, `app/services/feed_service.py`, `app/schemas/feed.py`, `app/core/feed_rev.py`.

Alle Endpunkte liegen unter `/api/v1/feed`, sind GET-only und erwarten den Header `X-API-Key`. Zeitstempel sind ISO 8601 in UTC mit `Z`; die Zeitzone der Organisation steht im Feld `timezone`.

## Endpunkte

| Endpunkt | Zweck |
|----------|-------|
| `GET /einsaetze` | Liste, Parameter siehe unten |
| `GET /einsaetze/{id}` | Ein Einsatz, 404 außerhalb des Org-Scopes |
| `GET /head` | `{rev, server_time, active_count, schema_version}` zum billigen Änderungs-Check |

Parameter der Liste: `status=active|closed|all` (Standard `active`), `since` (ISO 8601; liefert Einsätze, die seitdem begonnen oder beendet wurden), `limit` (1 bis 200, Standard 50), `include_exercises=true` (Standard `false`), `include` (kommagetrennt: `kraefte`, `wachen`, `board`, `objekt`; `objekt` ist immer enthalten). Das Detail kennt `include` und `include_exercises`.

## Antwort

Die Liste hat die Form `{schema_version, server_time, einsaetze: [...]}`. Jeder Einsatz enthält immer dieselben Schlüssel:

`id`, `nummer`, `alarm_type_code`, `status`, `is_exercise`, `phase` (`alarmiert`, `anfahrt`, `einsatzstelle`, `abschluss`), `started_at`, `closed_at`, `taken_over_at`, `departed_at`, `on_scene_at`, `ready_again_at`, `address_street`, `address_no`, `address_city`, `lat`, `lng`, `objekt` (`{id, name}` oder `null`), `kraefte`, `wachen`, `board`, `unit_count`, `timezone`.

`kraefte`, `wachen` und `board` sind `null`, wenn sie nicht angefordert wurden, und eine (ggf. leere) Struktur, wenn sie angefordert wurden.

- `kraefte[]`: `id`, `vehicle_code`, `name`, `type`, `is_external`, `org_short`, `unit_status`, `added_at`
- `wachen[]`: `wache_unid`, `wache_name`, `status`, `status_at`
- `board`: `columns[]` (`code`, `title`, `column_kind`, `display_order`), `tasks[]` (`id`, `title`, `status`, `is_done`, `done_at`, `is_cancelled`, `cancelled_at`, `due_at`, `created_at`, `column_code`, `vehicle_id`), `messages[]` (`id`, `title`, `status`, `is_done`, `done_at`, `is_cancelled`, `created_at`, `column_code`, `vehicle_id`)

Felder werden nur additiv ergänzt; ein Bruch erhöht `schema_version`.

## Caching mit ETag

Jede Antwort trägt einen `ETag`, `Cache-Control: private, no-cache` und `Vary: X-API-Key`. Mit `If-None-Match` (auch `W/"…"`, mehrere Werte oder `*`) antwortet der Server mit `304` ohne Body. Der ETag wird vor dem Aufbau der Antwort aus einer billigen Abfrage `(id, feed_rev, status)` der sichtbaren Einsätze berechnet und enthält Filter, Scopes und `include`; `server_time` fließt nicht ein.

## `feed_rev`

`Incident.feed_rev` zählt Änderungen am Einsatz-Snapshot. Der `after_flush`-Hook in `app/core/feed_rev.py` erhöht ihn pro Flush höchstens einmal je Einsatz, wenn Einsatz, `IncidentColumn`, `IncidentVehicle`, `IncidentWacheStatus`, `Task`, `Message` oder `ObjektEinsatz` angelegt, geändert oder gelöscht werden. Massenänderungen per `query.update()` oder `query.delete()` umgehen den Hook; sie sind für Mandantentabellen ohnehin verboten. Stammdaten ohne `incident_id` (z. B. `VehicleMaster`) erhöhen `feed_rev` nicht.

## Neues Feld ergänzen

1. Feld im DTO in `app/schemas/feed.py` und im Aufbau in `app/services/feed_service.py` ergänzen. Es wird explizit kopiert, nie per ORM-Dump.
2. Fachlich passenden Scope bestimmen; sensible Felder nicht in die Basis legen.
3. Neue Entitäten mit `incident_id` in `_CHILD_TYPES` in `app/core/feed_rev.py` aufnehmen und den Hook-Test erweitern.
4. `FORBIDDEN_FIELDS` pflegen und den tiefen Whitelist-Test in `tests/test_feed_erweiterungen.py` mit geseedeten Geheimnissen erweitern.
5. Testrouten und Feed-Routen müssen unter `/api/` liegen, sonst leitet der globale Handler 401 auf `/login` um. Tests, die Einsätze anlegen, müssen sie wieder löschen; andere Tests setzen auf „neuester Einsatz der Org“.
