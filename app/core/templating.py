"""Zentrale Jinja2Templates-Instanz inklusive Filter-Registry.

Alle Router importieren `templates` von hier statt eigene Instanzen zu bauen.
Damit teilen sie sich dieselbe Jinja-Environment und die Filter (z. B.
`local_time`, `local_datetime`) sind ueberall verfuegbar.

Die Zeitzonen-Filter lesen das User-Objekt aus dem Jinja-Kontext (immer als
`user` uebergeben), entnehmen dort `user.org` und formatieren das datetime in
der jeweiligen Org-Zeitzone. Faellt auf settings.DEFAULT_TIMEZONE zurueck,
wenn weder User noch Org-Zeitzone bekannt sind.
"""
import json as _json

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.core.timezones import (
    format_local_datetime,
    format_local_iso,
    format_local_time,
    to_org_tz,
)


def _ctx_org(ctx):
    user = ctx.get("user") if ctx else None
    return getattr(user, "org", None) if user else None


@pass_context
def _local_time(ctx, dt):
    return format_local_time(dt, _ctx_org(ctx))


@pass_context
def _local_datetime(ctx, dt):
    return format_local_datetime(dt, _ctx_org(ctx))


@pass_context
def _local_iso(ctx, dt):
    return format_local_iso(dt, _ctx_org(ctx))


@pass_context
def _local(ctx, dt):
    """Konvertiert ein datetime in die Org-Zeitzone (gibt datetime zurueck).
    Verwendung in Templates fuer exotische Formate:
        {{ (dt|local).strftime('%d.%m. %H:%M:%S') }}
    """
    return to_org_tz(dt, _ctx_org(ctx))


_ACTION_LABELS: dict[str, str] = {
    "incident.created":         "Einsatz gestartet",
    "incident.closed":          "Einsatz abgeschlossen",
    "column.created":           "Abschnitt angelegt",
    "vehicle.moved":            "Einheit verschoben",
    "vehicle.commander_set":    "Gruppenkommandant zugeteilt",
    "vehicle.status_set":       "Status geändert",
    "vehicle.updated":          "Einheit aktualisiert",
    "task.created":             "Auftrag angelegt",
    "task.updated":             "Auftrag bearbeitet",
    "task.assigned":            "Auftrag einer Einheit zugeteilt",
    "task.cancelled":           "Auftrag ausgeblendet",
    "task.restored":            "Auftrag wiederhergestellt",
    "task.status_set":          "Auftrag-Status geändert",
    "message.created":          "Meldung angelegt",
    "message.updated":          "Meldung bearbeitet",
    "message.status_set":       "Meldungs-Status geändert",
    "person.created":           "Person erfasst",
    "person.updated":           "Person bearbeitet",
    "incident.address_updated": "Adresse / Koordinaten aktualisiert",
}


def _action_label(action: str) -> str:
    return _ACTION_LABELS.get(action, action.replace(".", " → ").replace("_", " "))


def _unit_status_slug(value: str) -> str:
    """Wandelt 'Einsatz übernommen' → 'einsatz-uebernommen' für CSS-Klassen."""
    if not value:
        return "unknown"
    s = value.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(src, dst)
    return s.replace(" ", "-")


_PERSON_STATUS_LABELS = {
    "gefunden":         "🔴 Gefunden",
    "versorgt":         "🟠 Versorgt",
    "abtransportiert":  "🟢 Abtransportiert",
    "verstorben":       "⚫ Verstorben",
}


def _person_status_label(value: str) -> str:
    return _PERSON_STATUS_LABELS.get(value, value)


templates = Jinja2Templates(directory="app/templates")
templates.env.filters["local"] = _local
templates.env.filters["local_time"] = _local_time
templates.env.filters["local_datetime"] = _local_datetime
templates.env.filters["local_iso"] = _local_iso
templates.env.filters["action_label"] = _action_label
templates.env.filters["unit_status_slug"] = _unit_status_slug
templates.env.filters["person_status_label"] = _person_status_label

def _ordered_col_items(col, vehicles, tasks, messages, persons):
    """Gibt geordnete Liste von (kind, obj)-Tupeln zurück.
    Respektiert col.card_order wenn vorhanden, sonst: Fahrzeuge → Aufträge → Meldungen → Personen.
    Items, die in card_order stehen aber nicht mehr in der Spalte sind (verschoben/gelöscht),
    werden übersprungen. Neue Items (nicht in card_order) werden am Ende angehängt.
    """
    def _default():
        result = [('vehicle', v) for v in vehicles]
        result += [('task', t) for t in tasks]
        result += [('message', m) for m in messages]
        result += [('person', p) for p in persons]
        return result

    if not col.card_order:
        return _default()

    try:
        order = _json.loads(col.card_order)
    except Exception:
        return _default()

    v_map = {v.id: v for v in vehicles}
    t_map = {t.id: t for t in tasks}
    m_map = {m.id: m for m in messages}
    p_map = {p.id: p for p in persons}

    result = []
    seen_v, seen_t, seen_m, seen_p = set(), set(), set(), set()

    for item in order:
        kind = item.get('kind')
        uid = item.get('id')
        if kind == 'vehicle' and uid in v_map:
            result.append(('vehicle', v_map[uid]))
            seen_v.add(uid)
        elif kind == 'task' and uid in t_map:
            result.append(('task', t_map[uid]))
            seen_t.add(uid)
        elif kind == 'message' and uid in m_map:
            result.append(('message', m_map[uid]))
            seen_m.add(uid)
        elif kind == 'person' and uid in p_map:
            result.append(('person', p_map[uid]))
            seen_p.add(uid)

    # Neue Items (noch nicht in card_order) am Ende
    for v in vehicles:
        if v.id not in seen_v:
            result.append(('vehicle', v))
    for t in tasks:
        if t.id not in seen_t:
            result.append(('task', t))
    for m in messages:
        if m.id not in seen_m:
            result.append(('message', m))
    for p in persons:
        if p.id not in seen_p:
            result.append(('person', p))

    return result


templates.env.globals["ordered_col_items"] = _ordered_col_items

# Lagekarte.info URL-Hilfsfunktion für Templates
from app.services.lagekarte import resolve_lagekarte_url  # noqa: E402

templates.env.globals["lagekarte_url"] = resolve_lagekarte_url

# Globale Konfigurationswerte für Templates
from app.config import settings as _settings  # noqa: E402

templates.env.globals["WEATHER_ENABLED"] = _settings.WEATHER_ENABLED
