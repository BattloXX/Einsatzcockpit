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
    "message.assigned":         "Meldung einer Einheit zugeteilt",
    "person.created":           "Person erfasst",
    "person.updated":           "Person bearbeitet",
    "person.status_set":        "Personen-Status geändert",
    "column.title_set":         "Abschnitt umbenannt",
    "column.section_leader_set": "Abschnittsleiter zugewiesen",
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
    werden übersprungen. Neue Items (noch nicht in card_order, z. B. weil card_order aus
    irgendeinem Grund nicht via prepend_card() gepflegt wurde) werden ganz OBEN eingefügt,
    damit neue Karten immer zuerst sichtbar sind.
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

    # Neue Items (noch nicht in card_order) ganz oben einfügen, neueste zuerst.
    new_items = []
    for v in vehicles:
        if v.id not in seen_v:
            new_items.append(('vehicle', v))
    for t in tasks:
        if t.id not in seen_t:
            new_items.append(('task', t))
    for m in messages:
        if m.id not in seen_m:
            new_items.append(('message', m))
    for p in persons:
        if p.id not in seen_p:
            new_items.append(('person', p))

    return list(reversed(new_items)) + result


templates.env.globals["ordered_col_items"] = _ordered_col_items

# Lagekarte.info URL-Hilfsfunktion für Templates
from app.services.lagekarte import resolve_lagekarte_url  # noqa: E402

templates.env.globals["lagekarte_url"] = resolve_lagekarte_url

# Gefahren-Links (Katalog + objektspezifisch + generierte DB-Links) für Board-Meldungen etc.
from app.services.objekt_service import gefahr_links as _gefahr_links  # noqa: E402

templates.env.globals["gefahr_links"] = _gefahr_links

# Globale Konfigurationswerte für Templates
import os as _os  # noqa: E402

from app.config import settings as _settings  # noqa: E402

templates.env.globals["WEATHER_ENABLED"] = _settings.WEATHER_ENABLED
templates.env.globals["TEST_SYSTEM"] = _settings.TEST_SYSTEM

# Brand-Konstanten für Templates ({{ brand }}, {{ brand_tagline }}, {{ brand_domain }})
templates.env.globals["brand"] = _settings.APP_NAME
templates.env.globals["brand_tagline"] = _settings.APP_TAGLINE
templates.env.globals["brand_domain"] = _settings.APP_DOMAIN

# Öffentlicher Bereich: Login-/GitHub-/Kontakt-Ziele als Template-Globals
# ({{ public_login_url }}, {{ public_github_url }}, {{ public_contact_email }}).
templates.env.globals["public_login_url"] = _settings.PUBLIC_LOGIN_URL
templates.env.globals["public_github_url"] = _settings.PUBLIC_GITHUB_URL
templates.env.globals["public_contact_email"] = _settings.PUBLIC_CONTACT_EMAIL

# Cache-Busting: Versionsnummer aus mtime der app.css
_css_path = _os.path.join(_os.path.dirname(__file__), "..", "static", "css", "app.css")
try:
    templates.env.globals["CSS_VERSION"] = str(int(_os.path.getmtime(_css_path)))
except OSError:
    templates.env.globals["CSS_VERSION"] = "1"

# Cache-Busting für JS/Scripts: max(mtime) über alle app/static/js/*.js-Dateien.
# Ohne Versions-Query behielt der Service Worker (stale-while-revalidate, sw.js)
# nach einem Deploy die alte JS-Datei bis zum zweiten Seitenaufruf ("F5 nötig") –
# mit ?v=… erzwingt ein geänderter Board-Skript-Stand sofort einen neuen Cache-Key.
_js_dir = _os.path.join(_os.path.dirname(__file__), "..", "static", "js")
try:
    _js_mtimes = [
        _os.path.getmtime(_os.path.join(_js_dir, f))
        for f in _os.listdir(_js_dir)
        if f.endswith(".js")
    ]
    templates.env.globals["ASSET_VERSION"] = str(int(max(_js_mtimes))) if _js_mtimes else "1"
except OSError:
    templates.env.globals["ASSET_VERSION"] = "1"

# Cache-Busting für Icons/Favicons: NGINX liefert /static/ mit
# "Cache-Control: immutable" + 7 Tage Expiry direkt von der Platte aus (siehe
# deploy/nginx-snippet.conf) – ohne Versions-Query im Dateinamen/Query-String
# bleiben Browser bis zu 7 Tage beim alten Icon, auch nach einem Deploy.
# ACHTUNG: static/manifest.webmanifest wird ebenfalls von NGINX direkt
# ausgeliefert (kein Jinja) – der ?v=…-Query an den dortigen Icon-src-Werten
# muss beim naechsten Icon-Wechsel manuell im JSON nachgezogen werden.
_img_dir = _os.path.join(_os.path.dirname(__file__), "..", "static", "img")
try:
    _img_mtimes = [
        _os.path.getmtime(_os.path.join(_img_dir, f))
        for f in _os.listdir(_img_dir)
        if f.startswith(("icon-", "favicon"))
    ]
    templates.env.globals["IMG_VERSION"] = str(int(max(_img_mtimes))) if _img_mtimes else "1"
except OSError:
    templates.env.globals["IMG_VERSION"] = "1"
