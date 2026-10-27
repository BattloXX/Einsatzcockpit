"""Lokale Fehlerkorrelation ohne Quelltexte oder freie Logmeldungen in Antworten."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.admin_mcp.masking import mask_text

APP_ROOT = Path(__file__).resolve().parents[1]
PATH = re.compile(r"app/(?:[A-Za-z_][A-Za-z0-9_]*/)*[A-Za-z_][A-Za-z0-9_]*\.py")
FRAME = re.compile(r'File "([^"\n]{1,500})", line ([0-9]{1,7}), in ([A-Za-z_][A-Za-z0-9_]*|<module>)')
EXCEPTION = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))(?::|$)")
MODULE = re.compile(r"(?<![\w./\\])app(?:\.[A-Za-z_][A-Za-z0-9_]*){1,8}(?![\w.])")
CHECK_MODULES = {
    "gateway.jobs": ("app/services/gateway_*.py", "app/services/alarm_outbox.py"),
    "suche.rueckstand": ("app/services/search_*.py",),
    "worker.zustand": ("app/services/worker_monitor.py", "app/services/background.py"),
    "redis.zustand": ("app/services/ws_bus.py",),
    "db.zustand": ("app/db.py", "app/services/system_diagnose.py"),
    "deploy.version": ("app/config.py", "app/services/github_readonly.py"),
    "einsatz.integritaet": ("app/services/incident_service.py",),
    "kontakte.integritaet": ("app/models/kontakt.py",),
    "katplan.integritaet": ("app/services/katplan_*.py",),
    "objekte.dokumente": ("app/services/objekt_*.py",),
}


def local_path(raw: str) -> Path | None:
    if not isinstance(raw, str) or "\\" in raw or ".." in raw.split("/"):
        return None
    if raw.startswith(str(APP_ROOT) + "/"):
        raw = "app/" + raw[len(str(APP_ROOT)) + 1 :]
    if not PATH.fullmatch(raw):
        return None
    candidate = APP_ROOT.parent / raw
    try:
        # Keine Symlinks, auch nicht innerhalb des erlaubten Baums.
        current = APP_ROOT
        for segment in Path(raw).parts[1:]:
            current = current / segment
            if current.is_symlink():
                return None
        if not candidate.resolve().is_relative_to(APP_ROOT) or not candidate.is_file():
            return None
    except OSError:
        return None
    return candidate


def _place(raw: str, function: str = "<module>", line: int | None = None) -> dict | None:
    path = local_path(raw)
    if path is None:
        return None
    try:
        if path.stat().st_size > 512 * 1024:
            return None
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if function != "<module>" and function not in names:
            function = "unbekannt"
    except OSError, UnicodeError, SyntaxError:
        return None
    return {"datei": "app/" + str(path.relative_to(APP_ROOT)), "funktion": function, "zeile": line}


def modules_for_check(check_id: str) -> list[str]:
    result = []
    for pattern in CHECK_MODULES.get(check_id, ()):
        for path in sorted(APP_ROOT.glob(pattern.removeprefix("app/"))):
            relative = "app/" + str(path.relative_to(APP_ROOT))
            if local_path(relative):
                result.append(relative)
    return result[:50]


def extract_places(text: str) -> list[dict]:
    result = []
    for raw, line, function in FRAME.findall(text[:64000]):
        place = _place(raw, function, int(line))
        if place and place not in result:
            result.append(place)
    # Freie Pfade nur als vollstaendige relative Tokens akzeptieren: kein Herauspicken
    # von app/... aus ../../app/... oder fremden absoluten Pfaden.
    for token in re.findall(r'[^\s"\'(),;:=]+', text[:64000]):
        if PATH.fullmatch(token):
            place = _place(token)
            if place and not any(p["datei"] == place["datei"] for p in result):
                result.append(place)
    for match in MODULE.findall(text[:64000]):
        parts = match.split(".")
        for end in range(len(parts), 1, -1):
            raw = "/".join(parts[:end]) + ".py"
            place = _place(raw, parts[end] if end < len(parts) else "<module>")
            if place:
                if not any(p["datei"] == place["datei"] for p in result):
                    result.append(place)
                break
    return result[:50]


def group_logs(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for row in rows:
        text = str(row.get("msg", ""))[:64000]
        # Meldungen koennen beliebige Personendaten enthalten: auch nach mask_text
        # keine freien Meldungen ausgeben. Exception-Typ auf eingebaute Typen beschraenken.
        import builtins

        types = EXCEPTION.findall(text)
        exception = types[-1] if types and isinstance(getattr(builtins, types[-1], None), type) else "unbekannt"
        stamp = row.get("ts", "")
        stamp = stamp if isinstance(stamp, str) and re.fullmatch(r"[0-9TZ: .+-]{10,35}", stamp) else None
        seen = set()
        for place in extract_places(text):
            key = (place["datei"], place["funktion"], exception)
            if key in seen:
                continue
            seen.add(key)
            group = groups.setdefault(
                key,
                {
                    **place,
                    "exception_typ": exception,
                    "anzahl": 0,
                    "erstes_auftreten": stamp,
                    "letztes_auftreten": stamp,
                    "meldung": mask_text("Meldung aus Datenschutzgruenden nicht ausgegeben."),
                },
            )
            group["anzahl"] += 1
            if stamp:
                group["erstes_auftreten"] = min(group["erstes_auftreten"] or stamp, stamp)
                group["letztes_auftreten"] = max(group["letztes_auftreten"] or stamp, stamp)
    return sorted(groups.values(), key=lambda g: -g["anzahl"])[:100]


def finding_groups(finding) -> list[dict]:
    def texts(value, depth=0):
        if depth > 5:
            return []
        if isinstance(value, str):
            return [value[:64000]]
        if isinstance(value, dict):
            return [text for v in list(value.values())[:50] for text in texts(v, depth + 1)][:100]
        if isinstance(value, list):
            return [text for v in value[:50] for text in texts(v, depth + 1)][:100]
        return []

    places = extract_places("\n".join(texts(finding.evidenz))[:64000])
    for module in modules_for_check(finding.check_id):
        if not any(p["datei"] == module for p in places):
            places.append({"datei": module, "funktion": "<module>", "zeile": None})
    return [
        {
            **p,
            "exception_typ": "unbekannt",
            "anzahl": finding.occurrences,
            "erstes_auftreten": finding.first_seen_at.isoformat() + "Z",
            "letztes_auftreten": finding.last_seen_at.isoformat() + "Z",
        }
        for p in places[:50]
    ]
