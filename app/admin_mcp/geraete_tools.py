"""Lesende App-Diagnose mit begrenzter, maskierter MCP-Evidenz."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.admin_mcp.masking import mask_text
from app.models.admin_security import utcnow
from app.services import app_geraet_auswertung as auswertung
from app.services.diagnose.registry import MAX_IDS

DETAIL_LIMIT = 50
PERMISSIONS = (
    "benachrichtigungen", "akku_optimierung_aus", "vollbild_intent", "dnd_zugriff", "standort", "sms", "netz",
)


def _safe(value):
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value)
            return value
        except ValueError:
            pass  # Nur gueltige Zeitstempel unveraendert ausgeben.
        return value if auswertung.calver(value) else mask_text(value)[:253]
    if isinstance(value, dict):
        # Schluessel stammen aus dem festen Schema bzw. der Ingest-Whitelist.
        return {k: _safe(v) for k, v in value.items() if k != "geraetename"}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("Ungueltige ID oder Grenze")


def _short(row):
    g = row["geraet"]
    return {"geraet_id": g.id, "app_version": g.app_version, "android_version": g.android_version,
            "last_report_at": g.last_report_at}


def collect_geraete(db, name: str, **args) -> dict:
    if name == "admin_geraet_details":
        ident = args.get("geraet_id")
        _positive(ident)
        data = auswertung.liste(db, geraet_id=ident, page_size=1)
        if not data["geraete"]:
            return {"status": "ok", "evidence": {"geraet": None}}
        row = data["geraete"][0]
        g = row["geraet"]
        errors = sorted(row["fehler"], key=lambda f: (f.last_at, f.id), reverse=True)
        evidence = {"geraet_id": g.id, "org_id": g.org_id,
                    "version": {"ist": g.app_version, "version_code": g.version_code,
                                "build_variant": g.build_variant, "ampel": row["ampel"],
                                "soll_version": data["soll_version"]},
                    "eigenschaften": {key: getattr(g, key) for key in (
                        "hersteller", "modell", "geraeteklasse", "android_version", "sdk_int",
                        "sicherheitspatch", "aufloesung", "webview_version", "abi", "dichte_dpi")},
                    "geraet_details": g.geraet_details or {},
                    "einstellungen": g.einstellungen or {}, "schreibgeschuetzt": True,
                    "berechtigungen": g.berechtigungen or {}, "dienste": g.dienste or {},
                    "last_report_at": g.last_report_at,
                    "fehler_gesamt": len(errors), "fehler_begrenzt": len(errors) > DETAIL_LIMIT,
                    "fehler": [{"id": f.id, "komponente": f.komponente, "code": f.code, "text": f.text,
                                "anzahl": f.anzahl, "last_at": f.last_at} for f in errors[:DETAIL_LIMIT]],
                    "befunde": [{"code": code, "titel": auswertung.REGELN[code][0],
                                 "schweregrad": auswertung.REGELN[code][1]} for code in row["befunde"]]}
        status = row["gesundheit"]
    else:
        org_id, sdk_max = args.get("org_id"), args.get("sdk_max")
        limit, problems = args.get("limit", 50), args.get("nur_probleme", False)
        for value in (org_id, sdk_max):
            if value is not None:
                _positive(value)
        if type(limit) is not int or not 1 <= limit <= 100 or type(problems) is not bool:
            raise ValueError("Ungueltige Filter")
        data = auswertung.liste(db, org_id=org_id, sdk_max=sdk_max, nur_probleme=problems, page_size=None)
        rows = data["geraete"]
        cap = min(limit, MAX_IDS)
        distributions = {}
        for key in ("app_version", "android_version", "sdk_int", "hersteller", "geraeteklasse"):
            counts = Counter(getattr(r["geraet"], key) for r in rows)
            distributions[key] = {"werte": [{"wert": value, "anzahl": count}
                                           for value, count in counts.most_common(limit)],
                                  "gruppen_gesamt": len(counts), "begrenzt": len(counts) > limit}
        old = [_short(r) for r in rows if r["ampel"] == "veraltet"]
        stale = [_short(r) for r in rows if r["geraet"].last_report_at < utcnow() - timedelta(hours=48)]
        gaps = []
        for permission in PERMISSIONS:
            missing = "nein" if permission == "standort" else "keins" if permission == "netz" else False
            ids = sorted(r["geraet"].id for r in rows
                         if (r["geraet"].berechtigungen or {}).get(permission) == missing)
            if ids:
                gaps.append({"berechtigung": permission, "anzahl_geraete": len(ids),
                             "geraete_ids": ids[:cap], "begrenzt": len(ids) > cap})
        clusters = defaultdict(list)
        for row in rows:
            for error in row["fehler"]:
                clusters[error.komponente, error.code].append(error)
        grouped = []
        for (component, code), errors in sorted(clusters.items()):
            ids = sorted({f.geraet_id for f in errors})
            grouped.append({"komponente": component, "code": code, "anzahl_geraete": len(ids),
                            "anzahl": sum(f.anzahl for f in errors), "last_at": max(f.last_at for f in errors),
                            "geraete_ids": ids[:cap], "begrenzt": len(ids) > cap})
        evidence = {"anzahl_geraete": data["gesamt"], "soll_version": data["soll_version"],
                    "verteilungen": distributions, "veraltete_geraete": old[:cap],
                    "veraltete_gesamt": len(old), "veraltete_begrenzt": len(old) > cap,
                    "berechtigungsluecken": gaps, "fehler_cluster": grouped[:limit],
                    "fehler_cluster_gesamt": len(grouped), "fehler_cluster_begrenzt": len(grouped) > limit,
                    "ohne_meldung_48h": stale[:cap], "ohne_meldung_48h_gesamt": len(stale),
                    "ohne_meldung_48h_begrenzt": len(stale) > cap}
        status = next((s for s in ("fail", "warn") if any(r["gesundheit"] == s for r in rows)), "ok")
    if data["soll_version"] is None:
        evidence["hinweis"] = "Soll-Version unbekannt; GitHub-Abgleich derzeit nicht verfuegbar."
    return {"status": status, "evidence": _safe(evidence)}
