"""Inventar der Emoji-/Unicode-Icons im Einsatzcockpit (Icon-System, Phase P0).

Zaehlt alle Emoji- und Symbolzeichen im App-Code, ordnet sie ueber ``ZUORDNUNG`` einem
Tabler-Icon bzw. einer Sonderbehandlung zu und schreibt ``docs/konzepte/icon-inventar.md``.
Plan: ``docs/plans/icon-system-plan.md``.

Aufruf:
    .venv/bin/python scripts/icon_inventar.py            # schreibt docs/konzepte/icon-inventar.md
    .venv/bin/python scripts/icon_inventar.py --check    # Exit 1, wenn ein Zeichen fehlt
    .venv/bin/python scripts/icon_inventar.py --baseline # schreibt die Waechter-Baseline neu
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
ZIEL = ROOT / "docs" / "icon-inventar.md"
BASELINE = ROOT / "tests" / "data" / "icon_emoji_baseline.txt"
MIGRIER_KONTEXTE = ("markup", "js", "python", "css", "option")
TABLER_VERSION = "3.49.0"

EMOJI_RE = re.compile(
    "[←-⇿⌀-⏿①-⓿■-➿⬀-⯿"
    "\U0001f000-\U0001faff]"
)

# Sonderwerte statt Tabler-Name:
#   =status  Statuspunkt -> status_dot()-Komponente (Plan 4.6)
#   =text    typografisches Zeichen im Fliesstext/Kommentar, bleibt
#   =fw      Feuerwehr-/Fachzeichen ohne Tabler-Entsprechung, bleibt unveraendert
ZUORDNUNG: dict[str, str] = {
    # Warnung, Info, Bestaetigung
    "⚠": "alert-triangle", "ℹ": "info-circle", "❓": "help-circle", "💡": "bulb",
    "✓": "check", "✔": "check", "✅": "circle-check", "☑": "square-check", "☐": "square",
    "✕": "x", "✗": "x", "✖": "x", "❌": "circle-x", "⛔": "ban", "📵": "device-mobile-off",
    # Navigation, Bedienung
    "☰": "menu-2", "🔍": "search", "🔎": "zoom-in", "🔗": "link", "↗": "external-link",
    "▶": "player-play", "◀": "chevron-left", "▸": "chevron-right", "▼": "chevron-down",
    "▾": "chevron-down", "⌄": "chevron-down", "▲": "chevron-up", "⏸": "player-pause",
    "⏹": "player-stop", "⏺": "player-record", "↻": "refresh", "↺": "rotate", "🔄": "refresh",
    "🔁": "repeat", "↩": "arrow-back-up", "↶": "arrow-back-up", "↷": "arrow-forward-up",
    "🔀": "arrows-shuffle", "↕": "arrows-sort", "⬇": "download", "⭳": "download", "⬆": "upload",
    "⬅": "arrow-left", "↵": "corner-down-left", "➕": "plus", "💾": "device-floppy",
    "✥": "pointer", "⌫": "eraser", "▭": "square", "◯": "circle", "⭕": "circle-dashed",
    "✏": "pencil", "✎": "pencil", "📝": "note", "🗑": "trash", "📎": "paperclip",
    "🖨": "printer", "📤": "upload", "📥": "inbox", "📂": "folder-open", "🗂": "folders",
    "🗄": "archive", "📦": "package", "🧰": "box", "🔧": "tool", "🛠": "tools",
    "⚙": "settings", "🎛": "adjustments", "🧩": "puzzle", "🔌": "plug", "🔋": "battery",
    "🔑": "key", "🔒": "lock", "🔐": "lock-password", "🛡": "shield", "🆔": "id",
    "🏷": "tag", "👁": "eye", "🔔": "bell", "✨": "sparkles", "🎯": "target", "🧪": "flask",
    "🎲": "dice-5", "🎭": "masks-theater", "🎖": "award", "⭐": "star", "🚩": "flag",
    "🧭": "compass", "🌐": "world", "🌍": "world", "🛣": "route", "🚦": "traffic-lights",
    # Dokumente, Medien, Kommunikation
    "📄": "file-text", "📜": "history", "🧾": "receipt", "📑": "files", "📋": "clipboard-list",
    "📘": "book-2", "📚": "books", "📖": "book", "📊": "chart-bar", "📈": "chart-line",
    "📷": "camera", "📸": "camera", "🖼": "photo", "🎬": "movie", "📺": "device-tv",
    "🖥": "device-desktop", "💻": "device-laptop", "🪟": "brand-windows", "📱": "device-mobile",
    "📲": "device-mobile-message", "📟": "device-mobile-message", "📞": "phone",
    "📻": "radio", "📡": "antenna", "🎤": "microphone", "📢": "speakerphone", "📣": "speakerphone",
    "✉": "mail", "📧": "mail", "📨": "mail-forward", "💬": "message", "🧵": "messages",
    # Personen, Orte, Gebaeude
    "👥": "users", "👤": "user", "🫂": "users-group", "🙋": "hand-stop", "🧍": "user-check",
    "📍": "map-pin", "🗺": "map", "🏢": "building", "🏠": "home", "🏘": "buildings",
    "🏛": "building-bank", "🏚": "home-x", "🏥": "building-hospital", "🚪": "door",
    "🅿": "parking",
    # Zeit
    "⏰": "alarm", "🕐": "clock", "🕓": "clock", "⏱": "stopwatch", "⏳": "hourglass",
    "⌛": "hourglass", "📅": "calendar", "🗓": "calendar-event",
    # Einsatz, Gefahren, Fahrzeuge
    "🚒": "firetruck", "🚑": "ambulance", "🚁": "helicopter", "✈": "plane", "🚨": "urgent",
    "🔥": "flame", "💥": "bomb", "☣": "biohazard", "⚡": "bolt", "🫁": "lungs",
    "🛟": "lifebuoy", "🩸": "droplet-half-2", "🚧": "barrier-block", "🧯": "fire-extinguisher",
    "🚰": "fire-hydrant", "💧": "droplet", "🌊": "ripple", "⛰": "mountain", "🌲": "tree",
    "♻": "recycle", "🚗": "car", "🛗": "elevator", "🚻": "users-group", "📕": "book",
    "🌀": "=fw",
    # Ergaenzungen aus dem Lauf
    "⏩": "player-track-next", "ⓘ": "info-circle", "☎": "phone", "❔": "help-circle",
    "📏": "ruler", "📩": "mail-opened", "🤖": "robot", "🧊": "snowflake",
    # Wetter
    "🌧": "cloud-rain", "🌦": "cloud-rain", "⛈": "cloud-storm", "❄": "snowflake",
    "🌨": "cloud-snow", "☀": "sun", "🌤": "sun-low", "⛅": "cloud", "☁": "cloud",
    "🌫": "mist", "💨": "wind", "🌬": "wind", "🌡": "temperature",
    # Statuspunkte
    "🔴": "=status", "🟢": "=status", "🟡": "=status", "🟠": "=status", "⚪": "=status",
    "⚫": "=status", "●": "=status", "○": "=status",
    # Typografie (Fliesstext, Kommentare, Formeln)
    "→": "=text", "←": "=text", "↔": "=text", "↓": "=text", "↑": "=text", "⇒": "=text",
    "⇄": "=text", "↳": "=text", "➜": "=fw", "▬": "=fw",
    "①": "=text", "②": "=text", "③": "=text", "④": "=text",
    # Fachzeichen ohne Tabler-Entsprechung (Kartenlegende, Planzeichen) - bleiben
    "🪨": "=fw", "🪵": "=fw", "🧱": "=fw", "🌉": "=fw", "▣": "=fw", "△": "=fw", "◇": "=fw",
}

# Externe Kanaele/Nicht-UI: Emojis bleiben (Plan 1.4)
EXTERN_MUSTER = (
    "app/templates/email/", "app/templates/mailing/", "app/cli.py", "app/config.py",
    "app/db_weather.py",
    "app/services/teams_card.py", "app/services/road_closure_notify_service.py",
    "app/services/alarm_outbox.py", "app/services/sms_", "app/routers/ui_teams_bot.py",
    "app/mcp/",
)
VENDOR = {
    "alpine.min.js", "htmx.min.js", "quill.min.js", "chart.umd.min.js", "konva.min.js",
    "leaflet.min.js", "leaflet-geoman.min.js", "leaflet.markercluster.js", "sortable.min.js",
    "quill-cursors.js", "app.css",
}
ENDUNGEN = (".html", ".js", ".mjs", ".py", ".css")


def _dateien():
    for p in sorted(APP.rglob("*")):
        if not p.is_file() or p.suffix not in ENDUNGEN or "__pycache__" in p.parts:
            continue
        if p.name in VENDOR or p.name.endswith(".min.js") or "collab" in p.parts:
            continue
        yield p


def _kontext(rel: str, zeile: str) -> str:
    if rel.startswith(EXTERN_MUSTER):
        return "extern"
    s = zeile.strip()
    if rel.endswith(".py") and s.startswith("#"):
        return "kommentar"
    if s.startswith(("{#", "//", "/*", "*", "<!--")):
        return "kommentar"
    if "<option" in zeile:
        return "option"
    if rel.endswith(".py"):
        return "python"
    if rel.endswith((".js", ".mjs")):
        return "js"
    if rel.endswith(".css") or "content:" in zeile:
        return "css"
    return "markup"


def sammeln():
    treffer: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for p in _dateien():
        rel = p.relative_to(ROOT).as_posix()
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in EMOJI_RE.finditer(zeile):
                treffer[m.group()].append((rel, nr, _kontext(rel, zeile)))
    return treffer


def migrierbar_je_datei(treffer=None) -> Counter:
    """Noch zu migrierende UI-Zeichen je Datei (ohne =text/=fw, Kommentare, extern)."""
    treffer = sammeln() if treffer is None else treffer
    jedatei: Counter = Counter()
    for ch, fund in treffer.items():
        if ZUORDNUNG.get(ch, "").startswith(("=text", "=fw")):
            continue
        for f, _, k in fund:
            if k in MIGRIER_KONTEXTE:
                jedatei[f] += 1
    return jedatei


# Begruendung je Datei, die nach der Umstellung (P0-P11) bewusst Emojis behaelt.
# Jeder Baseline-Eintrag braucht hier einen Grund (geprueft von tests/test_icon_waechter.py).
_PIKTOGRAMM = "Gefahren-Piktogramme (fachliche Information, bleiben Emoji)"
AUSNAHME_GRUENDE: dict[str, str] = {
    "app/models/objekt.py": _PIKTOGRAMM,
    "app/routers/ui_incident.py": _PIKTOGRAMM,
    "app/routers/ui_infoscreen_alarm.py": _PIKTOGRAMM,
    "app/routers/ui_lagefuehrung.py": _PIKTOGRAMM,
    "app/static/js/einsatz_info.js": _PIKTOGRAMM,
    "app/static/js/lagefuehrung.js": _PIKTOGRAMM,
    "app/templates/objekt/_einsatz_inhalt.html": _PIKTOGRAMM,
    "app/templates/objekt/_gefahren.html": _PIKTOGRAMM,
    "app/templates/objekt/infoscreen_alarm.html": _PIKTOGRAMM,
    "app/templates/pdf/objektblatt.html": _PIKTOGRAMM,
    "app/services/objekt_service.py": "Seeds: Gefahren-Piktogramme und Merkmal-Altwerte (icon, neben icon_name)",
    "app/models/major_incident.py": "CROSS_MARKER_TYPE_ICON: Emoji-Feld fuer JSON/Druck und Fachzeichen",
    "app/services/katplan_typen.py": "typ_icon: Emoji-Feld fuer JSON-Kompatibilitaet neben icon_name",
    "app/templates/incident_major/karte.html": "Kartenlegende: farbige Fachzeichen (legItem)",
    "app/templates/weather/_data_section.html": "Trendpfeil im Fliesstext",
    "app/templates/pdf/_emoji_font_face.html": "Jinja-Kommentar (Glyphenliste der PDF-Schrift)",
    "app/services/incident_service.py": "Push-Benachrichtigungstexte (externer Kanal)",
    "app/routers/api_v1.py": "Benachrichtigungstext der API (externer Kanal)",
    "app/routers/ui_termin.py": "XLSX-Export (keine UI)",
    "app/seed_data.py": "CLI-Ausgabe",
}


def baseline_schreiben(jedatei: Counter) -> None:
    kopf = (
        "# Waechter tests/test_icon_waechter.py: verbliebene Emojis je Datei mit Begruendung.\n"
        "# Darf nur schrumpfen. Neu erzeugen: .venv/bin/python scripts/icon_inventar.py --baseline\n"
        "# Begruendungen stehen in AUSNAHME_GRUENDE in scripts/icon_inventar.py.\n"
    )
    zeilen = [
        f"{f} {n}  # {AUSNAHME_GRUENDE.get(f, 'OHNE BEGRUENDUNG')}" for f, n in sorted(jedatei.items())
    ]
    BASELINE.write_text(kopf + "\n".join(zeilen) + "\n", encoding="utf-8")


def _bericht(treffer) -> str:
    zeilen = [
        "# Icon-Inventar",
        "",
        f"Automatisch erzeugt von `scripts/icon_inventar.py` (Tabler Icons {TABLER_VERSION}).",
        "Plan und Regeln: [plans/icon-system-plan.md](plans/icon-system-plan.md).",
        "",
        "Ziel-Spalte: Tabler-Icon-Name, oder `=status` (Statuspunkt → `status_dot`), `=text`",
        "(Typografie, bleibt), `=fw` (Feuerwehr-/Fachzeichen, bleibt unverändert).",
        "Kontexte: `markup`, `js`, `python`, `css`, `option` (Emoji in `<option>` → entfernen),",
        "`kommentar` (bleibt), `extern` (SMS/Teams/Mail/MCP/Logs, bleibt).",
        "",
    ]
    ges = Counter()
    for fund in treffer.values():
        for _, _, k in fund:
            ges[k] += 1
    zeilen += ["## Summen nach Kontext", "", "| Kontext | Treffer |", "|---|---:|"]
    zeilen += [f"| {k} | {n} |" for k, n in ges.most_common()]
    zeilen += ["", "## Zuordnung", "", "| Zeichen | Ziel | Treffer | migrieren* | Dateien (Auszug) |",
               "|---|---|---:|---:|---|"]
    for ch, fund in sorted(treffer.items(), key=lambda x: -len(x[1])):
        ziel = ZUORDNUNG.get(ch, "**FEHLT**")
        offen = sum(1 for _, _, k in fund if k in MIGRIER_KONTEXTE)
        dateien = sorted({f for f, _, _ in fund})
        auszug = ", ".join(f"`{d.removeprefix('app/')}`" for d in dateien[:4])
        if len(dateien) > 4:
            auszug += f" … (+{len(dateien) - 4})"
        zeilen.append(f"| {ch} | `{ziel}` | {len(fund)} | {offen} | {auszug} |")
    zeilen += ["", "\\* ohne Kommentare und externe Kanäle.", "",
               "## Dateien mit den meisten migrierbaren Treffern", "",
               "| Datei | Treffer |", "|---|---:|"]
    jedatei = migrierbar_je_datei(treffer)
    zeilen += [f"| `{f}` | {n} |" for f, n in jedatei.most_common(60)]
    return "\n".join(zeilen) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()
    treffer = sammeln()
    fehlend = sorted(ch for ch in treffer if ch not in ZUORDNUNG)
    if args.check:
        if fehlend:
            print("Ohne Zuordnung:", " ".join(fehlend))  # noqa: T201
            return 1
        return 0
    if args.baseline:
        baseline_schreiben(migrierbar_je_datei(treffer))
        return 0
    ZIEL.write_text(_bericht(treffer), encoding="utf-8")
    print(f"{ZIEL.relative_to(ROOT)} geschrieben, {len(treffer)} Zeichen, ohne Zuordnung: {''.join(fehlend) or '-'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
