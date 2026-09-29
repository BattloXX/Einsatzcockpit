"""Rein lesende MCP-Werkzeuge für das Fahrtenbuch."""
from datetime import date
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.core.timezones import format_local_iso
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, Fahrtzweck, Zielort
from app.models.master import OrgSettings, VehicleMaster
from app.services.fahrtenbuch_query_service import gefilterte_fahrten_query
from app.services.fahrtenbuch_service import normalisiere_person_name

MAX_LIMIT = 200
MAX_AUSWERTUNG_ZEILEN = 1000
MAX_AUSWERTUNG_TAGE = 366


def fahrtenbuch_modul_aktiv(org_id: int, db: object) -> bool:
    return bool(
        db.query(OrgSettings)  # type: ignore[attr-defined]
        .filter(OrgSettings.org_id == org_id, OrgSettings.fahrtenbuch_modul_aktiv == True)  # noqa: E712
        .first()
    )


def _wert(value: object) -> object:
    return float(value) if isinstance(value, Decimal) else value


def _fahrt_daten(fahrt: Fahrt, org: object, *, mit_kette: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "id": fahrt.id,
        "zeitpunkt": format_local_iso(fahrt.zeitpunkt, org),
        "fahrzeug": {
            "id": fahrt.fahrzeug_id,
            "code": fahrt.fahrzeug.code if fahrt.fahrzeug else "",
            "name": fahrt.fahrzeug.name if fahrt.fahrzeug else "",
        },
        "maschinisten": [name for name in (fahrt.maschinist_name, fahrt.maschinist2_name) if name],
        "ausbildner_name": fahrt.ausbildner_name,
        "gruppenkommandant_name": fahrt.gruppenkommandant_name,
        "einsatzleiter_name": fahrt.einsatzleiter_name,
        "km_delta": fahrt.km_delta,
        "km_stand_neu": fahrt.km_stand_neu,
        "betriebsstunden_delta": _wert(fahrt.betriebsstunden_delta),
        "zweck": {"id": fahrt.zweck_id, "name": fahrt.zweck.name if fahrt.zweck else ""},
        "kategorie": fahrt.fahrttyp.value,
        "zielort": fahrt.zielort.name if fahrt.zielort else fahrt.zielort_freitext,
        "status": fahrt.status.value,
        "bemerkung": fahrt.bemerkung,
        "schaden": {
            "vorhanden": fahrt.schaden_vorhanden,
            "betriebsfaehig": fahrt.schaden_betriebsfaehig,
        },
    }
    if mit_kette:
        result["original_fahrt_id"] = fahrt.original_fahrt_id
        result["ersetzt_durch_id"] = fahrt.ersetzt_durch_id
    return result


@register_tool(
    name="fahrtenbuch_stammdaten",
    description="Liest sichere Fahrtenbuch-Stammdaten der eigenen Organisation.",
    required_roles=("fahrtenbuch_admin",),
    module_check=fahrtenbuch_modul_aktiv,
)
async def fahrtenbuch_stammdaten(context: MCPContext) -> dict[str, object]:
    fahrzeuge = (
        context.db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == context.org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.deleted == False,  # noqa: E712
            VehicleMaster.is_adhoc == False,  # noqa: E712
            VehicleMaster.is_external == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order, VehicleMaster.code)
        .all()
    )
    zwecke = (
        context.db.query(Fahrtzweck).filter(Fahrtzweck.aktiv == True)  # noqa: E712
        .order_by(Fahrtzweck.sort, Fahrtzweck.name).all()
    )
    zielorte = (
        context.db.query(Zielort).filter(Zielort.aktiv == True)  # noqa: E712
        .order_by(Zielort.sort, Zielort.name).all()
    )
    return {
        "fahrzeuge": [
            {"id": f.id, "code": f.code, "name": f.name, "typ": f.type, "kennzeichen": f.kennzeichen}
            for f in fahrzeuge
        ],
        "zwecke": [{"id": z.id, "name": z.name, "kategorie": z.kategorie.value} for z in zwecke],
        "kategorien": [{"id": k.value, "name": k.label} for k in FahrtKategorie],
        "zielorte": [{"id": z.id, "name": z.name} for z in zielorte],
    }


@register_tool(
    name="fahrtenbuch_fahrten",
    description="Listet Fahrten der eigenen Organisation mit sicheren Ausgabefeldern.",
    required_roles=("fahrtenbuch_admin",),
    module_check=fahrtenbuch_modul_aktiv,
)
async def fahrtenbuch_fahrten(
    context: MCPContext, von: str = "", bis: str = "", fahrzeug_id: int = 0,
    kategorie: str = "", zweck_id: int = 0, status: str = "aktiv", fahrer: str = "",
    nur_statistikrelevant: bool = False, limit: int = 50, seite: int = 1,
) -> dict[str, object]:
    if status not in ("aktiv", "alle"):
        raise ValueError("status muss 'aktiv' oder 'alle' sein.")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError("limit muss zwischen 1 und 200 liegen.")
    query = gefilterte_fahrten_query(
        context.db, context.org_id, context.user.org, von=von, bis=bis, fahrzeug_id=fahrzeug_id,
        fahrttyp=kategorie, zweck_id=zweck_id, status=status,
        nur_statistikrelevant=nur_statistikrelevant,
    )
    if fahrer.strip():
        suchtext = f"%{fahrer.strip()}%"
        query = query.filter(or_(Fahrt.maschinist_name.ilike(suchtext), Fahrt.maschinist2_name.ilike(suchtext)))
    gesamt = query.count()
    seite = max(1, seite)
    fahrten = query.order_by(Fahrt.zeitpunkt.desc()).offset((seite - 1) * limit).limit(limit).all()
    return {
        "fahrten": [_fahrt_daten(f, context.user.org) for f in fahrten],
        "seite": seite,
        "limit": limit,
        "gesamt": gesamt,
        "truncated": seite * limit < gesamt,
    }


@register_tool(
    name="fahrtenbuch_fahrt",
    description="Liest eine Fahrt samt Korrekturkette aus der eigenen Organisation.",
    required_roles=("fahrtenbuch_admin",),
    module_check=fahrtenbuch_modul_aktiv,
)
async def fahrtenbuch_fahrt(context: MCPContext, fahrt_id: int) -> dict[str, object]:
    fahrt = (
        context.db.query(Fahrt).filter(Fahrt.id == fahrt_id, Fahrt.org_id == context.org_id)
        .execution_options(include_all_tenants=True)
        .options(joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort)).first()
    )
    if not fahrt:
        raise ValueError("Die Fahrt wurde nicht gefunden.")
    return {"fahrt": _fahrt_daten(fahrt, context.user.org, mit_kette=True)}


@register_tool(
    name="fahrtenbuch_auswertung",
    description="Wertet aktive, statistikrelevante Fahrten der eigenen Organisation aus.",
    required_roles=("fahrtenbuch_admin",),
    module_check=fahrtenbuch_modul_aktiv,
)
async def fahrtenbuch_auswertung(
    context: MCPContext, von: str = "", bis: str = "", gruppierung: str = "fahrzeug",
    fahrzeug_id: int = 0, kategorie: str = "", zweck_id: int = 0,
) -> dict[str, object]:
    if gruppierung not in {"fahrzeug", "maschinist", "kategorie", "zweck", "monat"}:
        raise ValueError("gruppierung muss fahrzeug, maschinist, kategorie, zweck oder monat sein.")
    truncated = False
    if von and bis:
        try:
            if (date.fromisoformat(bis) - date.fromisoformat(von)).days > MAX_AUSWERTUNG_TAGE:
                truncated = True
                start = date.fromisoformat(von)
                bis = date.fromordinal(start.toordinal() + MAX_AUSWERTUNG_TAGE).isoformat()
        except ValueError:
            pass
    query = gefilterte_fahrten_query(
        context.db, context.org_id, context.user.org, von=von, bis=bis, fahrzeug_id=fahrzeug_id,
        fahrttyp=kategorie, zweck_id=zweck_id, status="aktiv", nur_statistikrelevant=True,
    )
    fahrten = query.order_by(Fahrt.zeitpunkt.desc()).limit(MAX_AUSWERTUNG_ZEILEN + 1).all()
    if len(fahrten) > MAX_AUSWERTUNG_ZEILEN:
        fahrten = fahrten[:MAX_AUSWERTUNG_ZEILEN]
        truncated = True
    gruppen: dict[str, dict[str, object]] = {}
    for fahrt in fahrten:
        if gruppierung == "fahrzeug":
            key, label = str(fahrt.fahrzeug_id), fahrt.fahrzeug.code if fahrt.fahrzeug else str(fahrt.fahrzeug_id)
        elif gruppierung == "maschinist":
            label = (fahrt.maschinist_name or "?").strip() or "?"
            key = (
                f"id:{fahrt.maschinist_member_id}"
                if fahrt.maschinist_member_id
                else f"name:{normalisiere_person_name(label)}"
            )
        elif gruppierung == "kategorie":
            key, label = fahrt.fahrttyp.value, fahrt.fahrttyp.label
        elif gruppierung == "zweck":
            key, label = str(fahrt.zweck_id), fahrt.zweck.name if fahrt.zweck else str(fahrt.zweck_id)
        else:
            key = format_local_iso(fahrt.zeitpunkt, context.user.org)[:7]
            label = key
        gruppe = gruppen.setdefault(
            key,
            {
                "label": label, "anzahl": 0, "einsatz": 0, "uebung": 0, "taetigkeit": 0,
                "sonstige": 0, "km_summe": 0, "betriebsstunden_summe": Decimal("0"),
            },
        )
        gruppe["anzahl"] = int(gruppe["anzahl"]) + 1  # type: ignore[call-overload]
        gruppe[fahrt.fahrttyp.value] = int(gruppe[fahrt.fahrttyp.value]) + 1  # type: ignore[call-overload]
        gruppe["km_summe"] = int(gruppe["km_summe"]) + int(fahrt.km_delta or 0)  # type: ignore[call-overload]
        gruppe["betriebsstunden_summe"] = (
            Decimal(str(gruppe["betriebsstunden_summe"]))
            + Decimal(str(fahrt.betriebsstunden_delta or 0))
        )
    zeilen = []
    for gruppe in gruppen.values():
        gruppe["betriebsstunden_summe"] = _wert(gruppe["betriebsstunden_summe"])
        zeilen.append(gruppe)
    return {"gruppierung": gruppierung, "zeilen": sorted(zeilen, key=lambda z: str(z["label"])), "truncated": truncated}
