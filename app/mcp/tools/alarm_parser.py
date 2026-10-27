"""Maskierte Alarmdiagnose und Entwurfsverwaltung fuer Gateway-Administratoren."""

import re
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from difflib import get_close_matches
from functools import lru_cache
from typing import Any

from limits import RateLimitItemPerHour
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.dependencies import gateway_effective_enabled
from app.mcp.context import MCPContext, mcp_effective_enabled
from app.mcp.registry import register_tool
from app.models.gateway import AlarmIngest, AlarmParserRegelsatz, AlarmParserTestfall
from app.models.master import FireDept, OrgSettings
from app.services.alarm_parser import STANDARD_REGELSATZ, AlarmParseResult, Regelsatz
from app.services.alarm_parser.anonymisieren import anonymisiere
from app.services.alarm_parser.katalog import baue_kontext
from app.services.alarm_parser.laufzeit import aktiver_regelsatz, lege_entwurf_an
from app.services.alarm_parser.maskierung import maskiere, maskiere_ausgabe
from app.services.alarm_parser.normalize import vergleichsform
from app.services.alarm_parser.probelauf import probelauf

# Explizite Whitelist: weder Anrufer noch Mannschaft oder Autoren aus JSON uebernehmen.
PARSER_FELDER = (
    "leitstellennummer", "stichwort", "alarm_type_code", "weitere_codes", "sonderkennzeichen",
    "gemeinde", "ortsteil", "strasse", "hausnummer", "adresszusatz", "lagezusatz", "meldebild",
    "zusatzinfo", "alarmzeit", "rest_text", "status", "qualitaet", "hinweise", "parser_version",
    "regelsatz_version", "ist_uebung", "bma", "melderbereich",
)
STATUS = {"needs_review", "error", "incomplete", "ok", "applied", "stored"}


def modul_aktiv(org_id: int, db: Session) -> bool:
    org = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(org and org.alarm_parser_mcp_aktiv and gateway_effective_enabled(org_id, db)
                and mcp_effective_enabled(org_id, db))


@lru_cache(maxsize=4)
def _limiter(redis_url: str) -> MovingWindowRateLimiter:
    # Wie die HTTP-Limits: gemeinsamer Redis-Storage bei REDIS_URL, sonst lokal.
    # Bei Redis-Ausfall KEIN neues lokales Kontingent: dieser Datenexport schliesst.
    return MovingWindowRateLimiter(storage_from_string(redis_url or "memory://"))


def _leselimit(context: MCPContext) -> None:
    try:
        erlaubt = _limiter(settings.REDIS_URL or "").hit(
            RateLimitItemPerHour(300), "alarm-parser-mcp", str(context.org_id), str(context.user.id),
        )
    except Exception as exc:
        raise ValueError("Leselimit derzeit nicht pruefbar. Bitte spaeter erneut versuchen.") from exc
    if not erlaubt:
        raise ValueError("Leselimit erreicht: maximal 300 Aufrufe pro Stunde und Benutzer.")


def _audit(context: MCPContext, werkzeug: str, anzahl: int, **filterwerte: Any) -> None:
    # Freitextfilter nur als gesetzte Filter protokollieren, niemals deren Inhalte.
    write_audit(context.db, "alarm_ingest.mcp_gelesen", org_id=context.org_id, user_id=context.user.id,
                payload={"werkzeug": werkzeug, "anzahl": anzahl, "filter": filterwerte})
    context.db.commit()


def _zeit(value: str) -> datetime | None:
    if not value:
        return None
    try:
        zeit = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("von/bis muessen ISO-Datum oder ISO-Zeit sein.") from exc
    return zeit.astimezone(UTC).replace(tzinfo=None) if zeit.tzinfo else zeit


def _query(context: MCPContext, von: str = "", bis: str = ""):
    start, ende = _zeit(von), _zeit(bis)
    if start and ende and start > ende:
        raise ValueError("von muss vor bis liegen.")
    query = context.db.query(AlarmIngest).filter(AlarmIngest.org_id == context.org_id)
    if start:
        query = query.filter(AlarmIngest.received_at >= start)
    if ende:
        query = query.filter(AlarmIngest.received_at <= ende)
    return query


def _qualitaet(row: AlarmIngest) -> int:
    value = (row.parsed or {}).get("qualitaet", 0)
    return value if type(value) is int and 0 <= value <= 100 else 0


def _parser(row: AlarmIngest) -> dict:
    daten = row.parsed or {}
    # Nur skalare Werte und Listen skalarer Werte; keine versteckten JSON-Unterobjekte.
    return maskiere_ausgabe({key: value for key in PARSER_FELDER if key in daten
                            and (isinstance(value := daten[key], (str, int, float, bool, type(None)))
                                 or isinstance(value, list) and all(isinstance(v, str) for v in value))}, row.raw_text)


def _seite(limit: int, seite: int) -> None:
    if not 1 <= limit <= 50 or seite < 1:
        raise ValueError("limit muss 1 bis 50 und seite mindestens 1 sein.")


def _auswahl(context: MCPContext, status: str = "", qualitaet_max: int | None = None,
             von: str = "", bis: str = "", leitstellennummer: str = "",
             nur_ohne_einsatz: bool = False):
    if any(not isinstance(value, str) for value in (status, von, bis, leitstellennummer)):
        raise ValueError("Status, Zeitgrenzen und Leitstellennummer muessen Text sein.")
    if type(nur_ohne_einsatz) is not bool:
        raise ValueError("nur_ohne_einsatz muss ein Wahrheitswert sein.")
    if status and status not in STATUS:
        raise ValueError("Unbekannter Status.")
    if qualitaet_max is not None and (type(qualitaet_max) is not int or not 0 <= qualitaet_max <= 100):
        raise ValueError("qualitaet_max muss zwischen 0 und 100 liegen.")
    query = _query(context, von, bis)
    if status == "ok":
        query = query.filter(or_(AlarmIngest.parsed["status"].as_string() == "ok",
                                 AlarmIngest.process_status == "ok"))
    elif status == "incomplete":
        query = query.filter(or_(AlarmIngest.parse_status == "incomplete",
                                 AlarmIngest.process_status == "incomplete"))
    elif status:
        query = query.filter(AlarmIngest.process_status == status)
    if leitstellennummer:
        query = query.filter(AlarmIngest.leitstellennummer == leitstellennummer)
    if nur_ohne_einsatz:
        query = query.filter(AlarmIngest.einsatz_id.is_(None))
    if qualitaet_max is not None:
        query = query.filter(AlarmIngest.parsed["qualitaet"].as_integer() <= qualitaet_max)
    return query


@register_tool(name="alarm_rohdaten_liste", description="Maskierte Rohalarme, maximal 50 je Seite; Admin.",
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_rohdaten_liste(
    context: MCPContext, status: str = "", qualitaet_max: int | None = None, von: str = "", bis: str = "",
    leitstellennummer: str = "", nur_ohne_einsatz: bool = False, limit: int = 50, seite: int = 1,
) -> dict[str, object]:
    _seite(limit, seite)
    _leselimit(context)
    query = _auswahl(context, status, qualitaet_max, von, bis, leitstellennummer, nur_ohne_einsatz)
    gesamt = query.count()
    rows = query.order_by(AlarmIngest.received_at.desc(), AlarmIngest.id.desc()).offset(
        (seite - 1) * limit).limit(limit).all()
    daten = []
    for row in rows:
        text = maskiere(row.raw_text)
        daten.append({"id": row.id, "zeit": row.received_at.isoformat(), "status": row.process_status,
                      "qualitaet": _qualitaet(row), "text": text[:240], "truncated": len(text) > 240})
    _audit(context, "alarm_rohdaten_liste", len(rows), status=status, qualitaet_max=qualitaet_max,
           von=_zeit(von), bis=_zeit(bis), leitstellennummer=bool(leitstellennummer),
           nur_ohne_einsatz=nur_ohne_einsatz, limit=limit, seite=seite)
    return {"rohdaten": daten, "gesamt": gesamt, "seite": seite, "truncated": seite * limit < gesamt}


@register_tool(name="alarm_rohdaten_lesen", description="Maskierter Rohalarm und Parserbefund der eigenen Org; Admin.",
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_rohdaten_lesen(context: MCPContext, ingest_id: int) -> dict[str, object]:
    _leselimit(context)
    row = _query(context).filter(AlarmIngest.id == ingest_id).first()
    if row is None:
        _audit(context, "alarm_rohdaten_lesen", 0, ingest_id=ingest_id)
        raise ValueError("Rohdaten nicht gefunden.")
    hextext = ""
    steuerzeichen: list[str] = []
    if row.raw_bytes:
        try:
            # Vollstaendig dekodieren/maskieren VOR dem Abschneiden (keine halben Nummern/Namen).
            decoded = row.raw_bytes.decode(row.charset or "utf-8", errors="strict")
            steuerzeichen = sorted({repr(char)[1:-1] for char in decoded[:512] if ord(char) < 32})
            hextext = maskiere(decoded, row.raw_text)
        except (UnicodeError, LookupError):
            # Ohne verlaessliche Dekodierung kann die Maskierung nicht greifen.
            hextext = ""
    auszug = hextext.encode("utf-8")[:512]
    versionen = []
    weitere = False
    if row.leitstellennummer:
        query = _query(context).filter(AlarmIngest.leitstellennummer == row.leitstellennummer,
                                       AlarmIngest.id != row.id)
        weitere = query.count() > 49
        versionen = [{"id": r.id, "zeit": r.received_at.isoformat()} for r in
                     query.order_by(AlarmIngest.received_at.desc(), AlarmIngest.id.desc()).limit(49)]
    result = {"id": row.id, "zeit": row.received_at.isoformat(), "status": row.process_status,
              "original_text": maskiere(row.raw_text), "hex_auszug": auszug.hex(" "),
              "bytes_sichtbar": repr(auszug.decode("utf-8", errors="replace"))[1:-1],
              "hex_truncated": len(hextext.encode("utf-8")) > 512, "steuerzeichen": steuerzeichen,
              "charset": maskiere(row.charset or ""),
              "parserergebnis": _parser(row), "parser_version": maskiere(row.parser_version or ""),
              "regelsatz_version": _parser(row).get("regelsatz_version", _parser(row).get("parser_version")),
              "versionen": versionen, "truncated": weitere, "einsatz_id": row.einsatz_id}
    _audit(context, "alarm_rohdaten_lesen", 1 + len(versionen), ingest_id=ingest_id)
    return result


@register_tool(name="alarm_parser_statistik", description="Kennzahlen und maskierte Alias-Kandidaten; Admin.",
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_statistik(context: MCPContext, von: str = "", bis: str = "") -> dict[str, object]:
    _leselimit(context)
    query = _query(context, von, bis)
    gesamt = query.count()
    # Ganze Zeitspanne aggregieren; keine einzelnen Rohdatensaetze ausgeben.
    rows = query.order_by(AlarmIngest.id).yield_per(50)
    status: Counter[str] = Counter()
    hinweise: Counter[str] = Counter()
    tokens: Counter[str] = Counter()
    gemeinden: Counter[str] = Counter()
    strassen: Counter[str] = Counter()
    for row in rows:
        daten = _parser(row)
        wert = daten.get("status", "unklar")
        status[wert if wert in {"ok", "teilweise", "unklar"} else "unklar"] += 1
        hinweise.update(daten.get("hinweise", []))
        rest = daten.get("rest_text", "")
        tokens.update(re.findall(r"\b[^\W\d_]{3,}\b", rest.casefold()))
        if rest and not daten.get("gemeinde"):
            gemeinden[rest[:160]] += 1
        if rest and not daten.get("strasse"):
            strassen[rest[:160]] += 1
    def haeufig(counter: Counter[str]) -> list[dict]:
        return [{"text": text, "anzahl": n} for text, n in counter.most_common(50)]
    _audit(context, "alarm_parser_statistik", gesamt, von=_zeit(von), bis=_zeit(bis))
    return {"gesamt": gesamt, "ausgewertet": gesamt, "truncated": False,
            "status": {key: {"anzahl": status[key], "anteil": status[key] / gesamt if gesamt else 0}
                       for key in ("ok", "teilweise", "unklar")},
            "hinweise": haeufig(hinweise), "rest_tokens": haeufig(tokens),
            "alias_kandidaten": {"gemeinden": haeufig(gemeinden), "strassen": haeufig(strassen)}}


@register_tool(name="alarm_parser_regelsatz_lesen", description="Regeln, Schema und begrenzte Ortskataloge; Admin.",
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_regelsatz_lesen(context: MCPContext, limit: int = 50, seite: int = 1) -> dict[str, object]:
    _seite(limit, seite)
    _leselimit(context)
    query = context.db.query(AlarmParserRegelsatz).filter(
        AlarmParserRegelsatz.org_id == context.org_id, AlarmParserRegelsatz.status == "entwurf")
    gesamt = query.count()
    rows = query.order_by(AlarmParserRegelsatz.version.desc()).offset((seite - 1) * limit).limit(limit).all()
    kontext = baue_kontext(context.db, context.org_id)
    katalog = [{"gemeinde": g.anzeigename, "ortsteile": list(g.ortsteile.values())[:50],
                "truncated": len(g.ortsteile) > 50} for g in list(kontext.gemeinden.values())[:50]]
    # Nur bekannte Regelfelder aus Entwuerfen; keine Notizen, Autoren oder beliebiges JSON.
    entwuerfe = [{"id": r.id, "version": r.version,
                  "regeln": {k: v for k, v in r.regeln.items() if k in Regelsatz.model_fields}} for r in rows]
    result = maskiere_ausgabe({"aktiv": aktiver_regelsatz(context.db, context.org_id).model_dump(mode="json"),
                              "entwuerfe": entwuerfe, "standard": STANDARD_REGELSATZ.model_dump(mode="json"),
                              "katalog": katalog})
    result.update({"schema": Regelsatz.model_json_schema(), "gesamt": gesamt, "seite": seite,
                   "truncated": seite * limit < gesamt, "katalog_truncated": len(kontext.gemeinden) > 50})
    _audit(context, "alarm_parser_regelsatz_lesen", len(rows), limit=limit, seite=seite)
    return result


@register_tool(name="alarm_ortsdaten_pruefen", description="Ortsfragmente gegen Org-Kataloge pruefen; Admin.",
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_ortsdaten_pruefen(
    context: MCPContext, gemeinde: str = "", ortsteil: str = "", strasse: str = "",
) -> dict[str, object]:
    if any(len(wert) > 200 for wert in (gemeinde, ortsteil, strasse)):
        raise ValueError("Fragmente duerfen maximal 200 Zeichen haben.")
    _leselimit(context)
    kontext = baue_kontext(context.db, context.org_id)
    gemeinden = kontext.gemeinden
    auswahl = [g for key, g in gemeinden.items() if not gemeinde or key == vergleichsform(gemeinde)]
    result = {}
    for art, fragment, werte in (
        ("gemeinde", gemeinde, [(key, g.anzeigename) for key, g in gemeinden.items()]),
        ("ortsteil", ortsteil, [(key, wert) for g in auswahl for key, wert in g.ortsteile.items()]),
        ("strasse", strasse, [(key, wert) for g in auswahl for key, wert in g.strassen.items()]),
    ):
        norm = vergleichsform(fragment)
        treffer = [wert for key, wert in werte if norm and key == norm]
        namen = dict(werte)
        aehnlich = get_close_matches(norm, list(namen), n=5, cutoff=0.5) if norm else []
        result[art] = {"treffer": treffer[:50], "mehrdeutig": len(treffer) > 1,
                       "aehnlich": [namen[key] for key in aehnlich], "truncated": len(treffer) > 50}
    _audit(context, "alarm_ortsdaten_pruefen", 0, gemeinde=bool(gemeinde),
           ortsteil=bool(ortsteil), strasse=bool(strasse))
    return maskiere_ausgabe(result)


def _org_sperren(context: MCPContext) -> None:
    # Gleiche Sperrreihenfolge wie die UI: Limits und Aenderungen atomar je Org.
    context.db.query(FireDept).filter(FireDept.id == context.org_id).with_for_update().one()


def _schreib_audit(context: MCPContext, action: str, row, werkzeug: str) -> None:
    write_audit(context.db, action, org_id=context.org_id, user_id=context.user.id,
                entity_type=row.__tablename__, entity_id=row.id,
                payload={"werkzeug": werkzeug})
    context.db.commit()


def _regeln_validieren(regeln: dict) -> Regelsatz:
    try:
        return Regelsatz.model_validate(regeln)
    except ValidationError as exc:
        # Pydantic-Fehler enthalten sonst Eingabewerte (ggf. Personendaten).
        meldungen = [f"{'.'.join(map(str, e['loc'])) or 'Regelsatz'}: {e['msg']}"
                     for e in exc.errors(include_input=False)]
        raise ValueError("Ungueltiger Regelsatz: " + "; ".join(meldungen)) from None


@register_tool(name="alarm_parser_entwurf_anlegen",
               description=("Kopie des aktiven Regelsatzes als Entwurf; keine Aktivierung."),
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_entwurf_anlegen(context: MCPContext, notiz: str) -> dict[str, object]:
    try:
        _org_sperren(context)
        offen = context.db.query(AlarmParserRegelsatz).filter(
            AlarmParserRegelsatz.org_id == context.org_id, AlarmParserRegelsatz.status == "entwurf",
            AlarmParserRegelsatz.quelle == "mcp",
        ).count()
        if offen >= 20:
            raise ValueError("Maximal 20 offene MCP-Entwuerfe je Organisation.")
        row = lege_entwurf_an(context.db, context.org_id, notiz=notiz, quelle="mcp",
                             erstellt_von_user_id=context.user.id)
        result = {"version": row.version, "status": row.status, "basis_version": row.basis_version}
        _schreib_audit(context, "alarm_parser.mcp_entwurf", row, "alarm_parser_entwurf_anlegen")
        return result
    except Exception:
        context.db.rollback()
        raise


def _regel_aendern(regeln: dict, aenderung: dict) -> None:
    if set(aenderung) - {"aktion", "feld", "wert", "schluessel"}:
        raise ValueError("Unbekannte Angaben in regel_aenderungen.")
    aktion, feld = aenderung.get("aktion"), aenderung.get("feld")
    if aktion not in ("setzen", "hinzufuegen", "entfernen"):
        raise ValueError("Aktion muss setzen, hinzufuegen oder entfernen sein.")
    if not isinstance(feld, str) or feld not in Regelsatz.model_fields or feld == "version":
        raise ValueError("Unbekanntes oder nicht aenderbares Regelfeld.")
    if aktion != "entfernen" and "wert" not in aenderung:
        raise ValueError("wert fehlt.")
    ziel = regeln[feld]
    key = aenderung.get("schluessel")
    wert = aenderung.get("wert")
    if isinstance(ziel, dict) and key is not None:
        if not isinstance(key, str):
            raise ValueError("schluessel muss Text sein.")
        if aktion == "hinzufuegen" and key in ziel:
            raise ValueError("Schluessel existiert bereits.")
        if aktion in ("setzen", "entfernen") and key not in ziel:
            raise ValueError("Schluessel nicht gefunden.")
        if aktion == "entfernen":
            del ziel[key]
        else:
            ziel[key] = wert
    elif key is not None:
        raise ValueError("schluessel ist nur fuer Objektfelder erlaubt.")
    elif aktion == "setzen":
        regeln[feld] = wert
    elif isinstance(ziel, list) and "wert" in aenderung:
        if aktion == "hinzufuegen":
            ziel.append(wert)
        elif wert in ziel:
            ziel.remove(wert)
        else:
            raise ValueError("Listeneintrag nicht gefunden.")
    else:
        raise ValueError("Hinzufuegen/Entfernen erfordert eine Liste oder einen Objektschluessel.")


@register_tool(name="alarm_parser_entwurf_aendern",
               description=("Einzelne Regelfelder eines UI-/MCP-Entwurfs aendern; vollstaendig validiert, keine "
                            "Aktivierung. setzen ersetzt Feld oder vorhandenen Objektschluessel; "
                            "hinzufuegen/entfernen bearbeitet Listenwerte oder Objektschluessel."),
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_entwurf_aendern(
    context: MCPContext, version: int, regel_aenderungen: list[dict], notiz: str | None = None,
) -> dict[str, object]:
    try:
        _org_sperren(context)
        row = context.db.query(AlarmParserRegelsatz).filter(
            AlarmParserRegelsatz.org_id == context.org_id, AlarmParserRegelsatz.version == version,
        ).populate_existing().with_for_update().one_or_none()
        if row is None or row.status != "entwurf" or row.quelle not in ("mcp", "ui"):
            raise ValueError("Nur UI-/MCP-Entwuerfe der eigenen Organisation sind aenderbar.")
        regeln = deepcopy(row.regeln)
        for aenderung in regel_aenderungen:
            _regel_aendern(regeln, aenderung)
        validiert = _regeln_validieren(regeln)
        row.regeln = validiert.model_dump(mode="json")
        if notiz is not None:
            row.notiz = notiz
        _schreib_audit(context, "alarm_parser.mcp_entwurf", row, "alarm_parser_entwurf_aendern")
        return {"version": version, "status": "entwurf"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="alarm_parser_testen",
               description=("Isolierter, rein lesender Probelauf eines Entwurfs oder aktiv gegen max. 200 "
                            "Rohalarme und alle aktiven Testfaelle. IDs oder Filter wie alarm_rohdaten_liste; "
                            "Antworten maskiert."),
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_testen(
    context: MCPContext, version: int | str, ingest_ids: list[int] | None = None,
    filter: dict | None = None, limit: int = 200,
) -> dict[str, object]:
    _leselimit(context)
    if not 1 <= limit <= 200 or ingest_ids is not None and len(ingest_ids) > 200:
        raise ValueError("Maximal 200 Rohdatensaetze; limit muss 1 bis 200 sein.")
    if ingest_ids is not None and filter is not None:
        raise ValueError("Entweder ingest_ids oder filter angeben.")
    filterwerte = filter or {}
    if set(filterwerte) - {"status", "qualitaet_max", "von", "bis", "leitstellennummer", "nur_ohne_einsatz"}:
        raise ValueError("Unbekannter Rohdatenfilter.")
    if version == "aktiv":
        regeln = aktiver_regelsatz(context.db, context.org_id)
    else:
        if type(version) is not int:
            raise ValueError("version muss eine Entwurfsnummer oder aktiv sein.")
        row = context.db.query(AlarmParserRegelsatz).filter(
            AlarmParserRegelsatz.org_id == context.org_id, AlarmParserRegelsatz.version == version,
            AlarmParserRegelsatz.status == "entwurf",
        ).first()
        if row is None:
            raise ValueError("Entwurf nicht gefunden.")
        regeln = _regeln_validieren({**row.regeln, "version": str(row.version)})
    query = _auswahl(context, **filterwerte)
    if ingest_ids is not None:
        query = query.filter(AlarmIngest.id.in_(ingest_ids))
    rows = query.order_by(AlarmIngest.received_at.desc(), AlarmIngest.id.desc()).limit(limit).all()
    ergebnis = probelauf(context.db, context.org_id, regeln, ingest_ids=[r.id for r in rows], letzte_n=limit)
    tests = context.db.query(AlarmParserTestfall).filter(
        AlarmParserTestfall.org_id == context.org_id, AlarmParserTestfall.aktiv.is_(True),
    ).all()
    referenz = "\n".join([r.raw_text or "" for r in rows] + [t.rohtext for t in tests])
    daten = []
    for datensatz in ergebnis.datensaetze:
        # Nur bekannte Parserfelder in Diffs und Erwartungsfehlern ausgeben.
        daten.append({**datensatz, "diff": [d for d in datensatz["diff"] if d["feld"] in PARSER_FELDER],
                      "erwartungsfehler": [d for d in datensatz["erwartungsfehler"] if d["feld"] in PARSER_FELDER]})
    result = maskiere_ausgabe({"datensaetze": daten, "zusammenfassung": ergebnis.zusammenfassung}, referenz)
    _audit(context, "alarm_parser_testen", len(daten), version=version, limit=limit,
           ingest_ids=ingest_ids, filter_gesetzt=sorted(filterwerte))
    return result


def _anonymisiere_wert(value: Any, referenz: str) -> Any:
    if isinstance(value, str):
        # Auch Namen ohne erneutes Notrufer-Praefix schuetzen.
        return maskiere(anonymisiere(value), referenz)
    if isinstance(value, list):
        return [_anonymisiere_wert(item, referenz) for item in value]
    return value


@register_tool(name="alarm_parser_testfall_anlegen",
               description=("Anonymisierten Testfall aus ingest_id ODER rohtext speichern. erwartet muss das vom "
                            "Menschen im Gespraech bestaetigte Ergebnis enthalten (bekannte AlarmParseResult- "
                            "Felder); keine automatisch geratenen Erwartungen."),
               required_roles=("admin",), module_check=modul_aktiv)
async def alarm_parser_testfall_anlegen(
    context: MCPContext, titel: str, erwartet: dict, ingest_id: int | None = None, rohtext: str | None = None,
) -> dict[str, object]:
    try:
        if (ingest_id is None) == (rohtext is None):
            raise ValueError("Genau eines von ingest_id oder rohtext angeben.")
        text = rohtext or ""
        if ingest_id is not None:
            ingest = _query(context).filter(AlarmIngest.id == ingest_id).first()
            if ingest is None:
                raise ValueError("Rohdaten nicht gefunden.")
            text = ingest.raw_text or ""
        if not titel.strip() or len(titel) > 200 or not text.strip() or len(text) > 65536:
            raise ValueError("Titel (max. 200) und Rohtext (max. 64 KiB) erforderlich.")
        felder = AlarmParseResult(original_text="").to_dict()
        if not erwartet or set(erwartet) - felder.keys():
            raise ValueError("erwartet muss bekannte AlarmParseResult-Felder enthalten.")
        for key, value in erwartet.items():
            standard = felder[key]
            typ = type(standard) if standard is not None else str
            if value is not None and (type(value) is not typ or
                                      isinstance(value, list) and any(type(v) is not str for v in value)):
                raise ValueError("Falscher Typ in erwartet.")
        _org_sperren(context)
        anzahl = context.db.query(AlarmParserTestfall).filter(
            AlarmParserTestfall.org_id == context.org_id, AlarmParserTestfall.aktiv.is_(True),
        ).count()
        if anzahl >= 500:
            raise ValueError("Maximal 500 aktive Testfaelle je Organisation.")
        row = AlarmParserTestfall(
            org_id=context.org_id, titel=_anonymisiere_wert(titel, text)[:200], rohtext=anonymisiere(text),
            erwartet={k: _anonymisiere_wert(v, text) for k, v in erwartet.items()}, ingest_id=ingest_id,
            quelle="mcp", erstellt_von_user_id=context.user.id, aktiv=True,
        )
        context.db.add(row)
        context.db.flush()
        result: dict[str, object] = {"testfall_id": row.id, "aktiv": True}
        _schreib_audit(context, "alarm_parser.mcp_testfall", row, "alarm_parser_testfall_anlegen")
        return result
    except Exception:
        context.db.rollback()
        raise
