"""MCP-Werkzeuge fuer Wasserstellen der eigenen Organisation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_

from app.config import settings
from app.core.timezones import now_local
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.master import FireDept
from app.models.wasserstelle import WASSERSTELLE_STATUS, WASSERSTELLE_TYPEN, Wasserstelle
from app.services.hydrant_service import _haversine_m
from app.services.wasserstelle_service import aktualisiere_wasserstelle, erstelle_wasserstelle

_FELDER = {"bezeichnung", "typ", "lat", "lng", "hinweis", "ergiebigkeit_l_min", "status"}


def _dict(w: Wasserstelle, entfernung_m: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "id": w.id, "bezeichnung": w.bezeichnung, "typ": w.typ, "typ_label": w.typ_label,
        "status": w.status, "status_label": w.status_label, "aktiv": w.aktiv, "lat": w.lat,
        "lng": w.lng, "ergiebigkeit_l_min": w.ergiebigkeit_l_min, "hinweis": w.hinweis,
        "quelle": w.quelle,
    }
    if entfernung_m is not None:
        result["entfernung_m"] = entfernung_m
    return result


def _wasserstelle(context: MCPContext, wasserstelle_id: int) -> Wasserstelle:
    w = context.db.query(Wasserstelle).filter(
        Wasserstelle.id == wasserstelle_id, Wasserstelle.org_id == context.org_id
    ).first()
    if w is None:
        raise ValueError("Wasserstelle nicht gefunden.")
    return w


def _iso_z(wert: datetime | None) -> str | None:
    if wert is None:
        return None
    if wert.tzinfo is not None:
        wert = wert.astimezone(UTC).replace(tzinfo=None)
    return wert.isoformat() + "Z"


@register_tool(
    name="wasserstellen_suchen",
    description="Sucht Wasserstellen der eigenen Organisation, optional im Umkreis.",
    required_roles=("org_admin",),
)
async def wasserstellen_suchen(
    context: MCPContext, q: str = "", typ: str = "", status: str = "", nur_aktive: bool = False,
    lat: float | None = None, lng: float | None = None, radius_m: int | None = None,
    limit: int = 50, seite: int = 1,
) -> dict[str, object]:
    try:
        if not 1 <= limit <= 200:
            raise ValueError("limit muss zwischen 1 und 200 liegen.")
        if seite < 1:
            raise ValueError("seite muss mindestens 1 sein.")
        if (lat is None) != (lng is None):
            raise ValueError("lat und lng müssen gemeinsam angegeben werden.")
        if radius_m is not None and (lat is None or lng is None):
            raise ValueError("radius_m erfordert lat und lng.")
        if typ and typ not in WASSERSTELLE_TYPEN:
            raise ValueError("Ungültiger Wasserstellen-Typ.")
        if status and status not in WASSERSTELLE_STATUS:
            raise ValueError("Ungültiger Wasserstellen-Status.")
        query = context.db.query(Wasserstelle).filter(Wasserstelle.org_id == context.org_id)
        if q.strip():
            pattern = f"%{q.strip()}%"
            query = query.filter(or_(Wasserstelle.bezeichnung.ilike(pattern), Wasserstelle.hinweis.ilike(pattern)))
        if typ:
            query = query.filter(Wasserstelle.typ == typ)
        if status:
            query = query.filter(Wasserstelle.status == status)
        if nur_aktive:
            query = query.filter(Wasserstelle.aktiv.is_(True))
        rows = query.all()
        mit_entfernung: list[tuple[Wasserstelle, int | None]] = []
        if lat is not None and lng is not None:
            for w in rows:
                if w.lat is None or w.lng is None:
                    continue
                distanz = int(round(_haversine_m(lat, lng, w.lat, w.lng)))
                if radius_m is None or distanz <= radius_m:
                    mit_entfernung.append((w, distanz))
            mit_entfernung.sort(key=lambda item: item[1] if item[1] is not None else 0)
        else:
            mit_entfernung = [(w, None) for w in sorted(rows, key=lambda item: (item.typ, item.bezeichnung))]
        gesamt = len(mit_entfernung)
        start = (seite - 1) * limit
        return {"gesamt": gesamt, "seite": seite, "limit": limit,
                "wasserstellen": [_dict(w, d) for w, d in mit_entfernung[start:start + limit]],
                "typen": WASSERSTELLE_TYPEN, "status_werte": WASSERSTELLE_STATUS}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="wasserstelle_lesen",
    description="Liest eine Wasserstelle der eigenen Organisation.",
    required_roles=("org_admin",),
)
async def wasserstelle_lesen(context: MCPContext, wasserstelle_id: int) -> dict[str, object]:
    try:
        w = _wasserstelle(context, wasserstelle_id)
        return _dict(w) | {"erstellt_am": _iso_z(w.erstellt_am), "aktualisiert_am": _iso_z(w.aktualisiert_am)}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="wasserstelle_anlegen",
    description="Legt eine Wasserstelle an und prüft räumliche bzw. namensgleiche Dubletten.",
    required_roles=("org_admin",),
)
async def wasserstelle_anlegen(
    context: MCPContext, bezeichnung: str, typ: str, lat: float | None, lng: float | None,
    hinweis: str = "", ergiebigkeit_l_min: int | None = None, status: str = "bereit",
    duplikat_bestaetigt: bool = False,
) -> dict[str, object]:
    try:
        name = bezeichnung.strip().lower()
        kandidaten: list[dict[str, object]] = []
        for w in context.db.query(Wasserstelle).filter(Wasserstelle.org_id == context.org_id).all():
            gleiche_bezeichnung = w.bezeichnung.strip().lower() == name
            entfernung: int | None = None
            gleicher_typ_nah = False
            if lat is not None and lng is not None and w.lat is not None and w.lng is not None:
                entfernung = int(round(_haversine_m(lat, lng, w.lat, w.lng)))
                gleicher_typ_nah = w.typ == typ and entfernung <= 15
            if gleiche_bezeichnung or gleicher_typ_nah:
                kandidaten.append({"id": w.id, "bezeichnung": w.bezeichnung, "entfernung": entfernung})
        if kandidaten and not duplikat_bestaetigt:
            raise ValueError(
                "Mögliche Dublette: " + str(kandidaten) + ". Mit duplikat_bestaetigt=true trotzdem anlegen."
            )
        w = erstelle_wasserstelle(
            context.db, org_id=context.org_id, user_id=context.user.id,
            daten={"bezeichnung": bezeichnung, "typ": typ, "lat": lat, "lng": lng, "hinweis": hinweis,
                   "ergiebigkeit_l_min": ergiebigkeit_l_min, "status": status},
            quelle="manuell", audit_aktion="wasserstelle.mcp_angelegt",
        )
        context.db.commit()
        return _dict(w) | {"ui_link": f"{settings.effective_public_base_url.rstrip('/')}/admin/wasserstellen"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="wasserstelle_aktualisieren",
    description="Aktualisiert die angegebenen Felder einer Wasserstelle.",
    required_roles=("org_admin",),
)
async def wasserstelle_aktualisieren(context: MCPContext, wasserstelle_id: int, felder: dict) -> dict[str, object]:
    try:
        unbekannt = set(felder) - _FELDER
        if unbekannt:
            raise ValueError(
                "Unbekannte Felder: " + ", ".join(sorted(unbekannt))
                + ". Erlaubt: " + ", ".join(sorted(_FELDER))
            )
        w = _wasserstelle(context, wasserstelle_id)
        geaendert = aktualisiere_wasserstelle(
            context.db, w, user_id=context.user.id, daten=felder, audit_aktion="wasserstelle.mcp_aktualisiert"
        )
        context.db.commit()
        return {"wasserstelle": _dict(w), "geaenderte_felder": geaendert}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="wasserstelle_deaktivieren",
    description="Markiert eine Wasserstelle als defekt und damit inaktiv.",
    required_roles=("org_admin",),
)
async def wasserstelle_deaktivieren(context: MCPContext, wasserstelle_id: int, grund: str = "") -> dict[str, object]:
    try:
        w = _wasserstelle(context, wasserstelle_id)
        felder: dict[str, object] = {"status": "defekt"}
        if grund.strip():
            org = context.db.get(FireDept, context.org_id)
            datum = now_local(org).date().isoformat() if org is not None else datetime.now(UTC).date().isoformat()
            zusatz = f"[Deaktiviert {datum}] {grund.strip()}"
            felder["hinweis"] = f"{w.hinweis}\n{zusatz}" if w.hinweis else zusatz
        aktualisiere_wasserstelle(
            context.db, w, user_id=context.user.id, daten=felder, audit_aktion="wasserstelle.mcp_deaktiviert"
        )
        context.db.commit()
        return _dict(w) | {"reaktivieren": "wasserstelle_aktualisieren mit felder={\"status\": \"bereit\"}"}
    except Exception:
        context.db.rollback()
        raise
