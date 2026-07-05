"""Einmalige Datenmigration: EUS-Objekte (fweus.at) → Einsatzcockpit-Objektverwaltung.

Liest den JSON-Export von eus_export_objekte.py und schreibt Kategorien,
Merkmal-Katalog, Objekte, BMA-Bloecke, Kontakte und Merkmal-Zuordnungen in die
bestehenden Tabellen (app/models/objekt.py). Dokumente werden in dieser Phase
nur gezaehlt (Phase 2: BLOB-Export + Upload-Pipeline).

Verwendung:
    python scripts/eus_import.py --input eus_objekte_export.json --org-id 1
    python scripts/eus_import.py --input eus_objekte_export.json --org-id 1 --dry-run
    python scripts/eus_import.py --input eus_objekte_export.json --org-id 1 --skip-existing

Idempotenz: --skip-existing ueberspringt Objekte, die es (name+strasse+
hausnummer je Org) schon gibt; Kategorien/Merkmale werden immer per Name
dedupliziert. Jedes Objekt laeuft in einem eigenen try/except — Fehler werden
gesammelt, der Import laeuft weiter. Detail-Log: eus_import_log.json.

Abweichungen zur urspruenglichen Task-Spezifikation (Modell-Realitaet):
- objekt.nummer ist Pflicht und unique je Org → EUS-objektnummer wird
  uebernommen wenn frei, sonst fortlaufend vergeben
- Die Merkmal-Zuordnung heisst ObjektMerkmal.merkmal_id (nicht katalog_id)
- Alle Kind-Tabellen sind TenantScoped → org_id wird ueberall mitgesetzt
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.master import FireDept  # noqa: E402
from app.models.objekt import (  # noqa: E402
    KONTAKT_ARTEN,
    OBJEKT_STATUS_FREIGEGEBEN,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektKategorie,
    ObjektKontakt,
    ObjektMerkmal,
)

LOG_DATEI = Path("eus_import_log.json")


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def parse_datum(wert) -> date | None:
    """ISO-Datum/-Datetime → date; None bei leer/ungueltig."""
    if not wert:
        return None
    try:
        return datetime.fromisoformat(str(wert).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(wert)[:10])
        except ValueError:
            return None


def _normalisiere(text: str) -> str:
    """lowercase, strip, Umlaute/Akzente ersetzen (fuer das Kontakt-Art-Mapping)."""
    t = text.strip().lower()
    t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def map_kontakt_art(eus_art: str | None) -> str:
    """EUS-Kontaktart → EC-Enum (KONTAKT_ARTEN); Unbekanntes → 'sonstig'."""
    if not eus_art:
        return "sonstig"
    norm = _normalisiere(eus_art)
    mapping = {
        "brandschutzbeauftragter": "brandschutzbeauftragter",
        "betreiber": "betreiber",
        "hausverwaltung": "hausverwaltung",
        "schluesseltraeger": "schluesseltraeger",
    }
    art = mapping.get(norm, "sonstig")
    return art if art in KONTAKT_ARTEN else "sonstig"


def _kuerze(wert, laenge: int) -> str | None:
    """String trimmen + auf Spaltenlaenge kuerzen; leere Werte → None."""
    if wert is None:
        return None
    text = str(wert).strip()
    return text[:laenge] if text else None


# ── Import-Schritte ────────────────────────────────────────────────────────────

def importiere_kategorien(db, org_id: int, kategorien: list[dict], stats: dict) -> dict[str, int]:
    """Schritt 1: Kategorien anlegen (Name-dedupliziert). eus_id → EC-ID."""
    mapping: dict[str, int] = {}
    for k in kategorien:
        name = _kuerze(k.get("name"), 100)
        if not name:
            continue
        vorhanden = (
            db.query(ObjektKategorie)
            .filter(ObjektKategorie.org_id == org_id, ObjektKategorie.name == name)
            .first()
        )
        if vorhanden:
            mapping[k["eus_id"]] = vorhanden.id
            stats["kategorien_vorhanden"] += 1
            continue
        neu = ObjektKategorie(org_id=org_id, name=name, sort=0,
                              aktiv=bool(k.get("aktiv", True)))
        db.add(neu)
        db.flush()
        mapping[k["eus_id"]] = neu.id
        stats["kategorien_neu"] += 1
    return mapping


def importiere_merkmale(db, org_id: int, merkmale: list[dict], stats: dict) -> dict[str, int]:
    """Schritt 2: Merkmal-Katalog anlegen (Name-dedupliziert). eus_id → EC-ID."""
    mapping: dict[str, int] = {}
    for m in merkmale:
        name = _kuerze(m.get("name"), 100)
        if not name:
            continue
        vorhanden = (
            db.query(MerkmalKatalog)
            .filter(MerkmalKatalog.org_id == org_id, MerkmalKatalog.name == name)
            .first()
        )
        if vorhanden:
            mapping[m["eus_id"]] = vorhanden.id
            stats["merkmale_katalog_vorhanden"] += 1
            continue
        neu = MerkmalKatalog(org_id=org_id, code=_kuerze(m.get("code"), 40),
                             name=name, sort=0, aktiv=True)
        db.add(neu)
        db.flush()
        mapping[m["eus_id"]] = neu.id
        stats["merkmale_katalog_neu"] += 1
    return mapping


class Nummernvergabe:
    """Objekt-Nummern je Org: EUS-Nummer uebernehmen wenn frei, sonst MAX+1."""

    def __init__(self, db, org_id: int):
        from sqlalchemy import func
        self._vergeben: set[int] = {
            n for (n,) in db.query(Objekt.nummer).filter(Objekt.org_id == org_id).all()
        }
        max_n = db.query(func.max(Objekt.nummer)).filter(Objekt.org_id == org_id).scalar()
        self._naechste = (max_n or 0) + 1

    def vergib(self, wunsch) -> int:
        try:
            wunsch_int = int(wunsch) if wunsch is not None else None
        except (ValueError, TypeError):
            wunsch_int = None
        if wunsch_int and wunsch_int > 0 and wunsch_int not in self._vergeben:
            self._vergeben.add(wunsch_int)
            return wunsch_int
        while self._naechste in self._vergeben:
            self._naechste += 1
        nummer = self._naechste
        self._vergeben.add(nummer)
        self._naechste += 1
        return nummer


def importiere_objekt(
    db, org_id: int, o: dict,
    kategorie_mapping: dict, merkmal_mapping: dict,
    nummern: Nummernvergabe,
) -> dict:
    """Schritt 3: ein Objekt inkl. BMA, Kontakten und Merkmalen anlegen."""
    jetzt = datetime.now(UTC)
    objekt = Objekt(
        org_id=org_id,
        nummer=nummern.vergib(o.get("objektnummer")),
        name=_kuerze(o.get("name"), 200) or f"EUS-Objekt {o.get('eus_objekt_daten_id')}",
        kategorie_id=kategorie_mapping.get(o.get("kategorie_eus_id")),
        strasse=_kuerze(o.get("strasse"), 200),
        hausnummer=_kuerze(o.get("hausnummer"), 20),
        plz=_kuerze(o.get("plz"), 10),
        ort=_kuerze(o.get("ort"), 100),
        lat=float(o["lat"]) if o.get("lat") is not None else None,
        lng=float(o["lng"]) if o.get("lng") is not None else None,
        informationen=(str(o["informationen"]).strip() or None) if o.get("informationen") else None,
        anfahrtsweg=(str(o["anfahrtsweg"]).strip() or None) if o.get("anfahrtsweg") else None,
        revision_datum=parse_datum(o.get("revision_datum")),
        status=OBJEKT_STATUS_FREIGEGEBEN,
        erstellt_am=jetzt,
        aktualisiert_am=jetzt,
    )
    db.add(objekt)
    db.flush()

    warnungen: list[str] = []
    detail: dict = {
        "eus_objekt_daten_id": o.get("eus_objekt_daten_id"),
        "name": objekt.name,
        "ec_objekt_id": objekt.id,
        "nummer": objekt.nummer,
        "status": "importiert",
        "bma": False,
        "kontakte": 0,
        "merkmale": 0,
        "dokumente_phase2": len(o.get("dokumente") or []),
        "warnungen": warnungen,
    }

    # 3c) BMA-Block
    bma = o.get("bma")
    if bma:
        db.add(ObjektBMA(
            org_id=org_id,
            objekt_id=objekt.id,
            bma_nummer=_kuerze(bma.get("bma_nummer"), 50),
            rfl_nummer=_kuerze(bma.get("rfl_nummer"), 50),
            bmz_standort=_kuerze(bma.get("bmz_standort"), 300),
            fbf_standort=_kuerze(bma.get("fbf_standort"), 300),
            laufkarten_ablageort=_kuerze(bma.get("laufkarten_ablageort"), 300),
            schluesselsafe_vorhanden=bool(bma.get("schluesselsafe_vorhanden")),
            schluesselsafe_standort=_kuerze(bma.get("schluesselsafe_standort"), 300),
            schluesselsafe_inhalt=_kuerze(bma.get("schluesselsafe_inhalt"), 300),
            benachrichtigung_sms=_kuerze(bma.get("benachrichtigung_sms"), 100),
        ))
        detail["bma"] = True
        # EUS: bma_nummer ist teils Freitext ("interne BMA, kein FBF...") → Warnung
        roh = str(bma.get("bma_nummer") or "").strip()
        if roh and not roh.replace("/", "").replace("-", "").replace(" ", "").isdigit():
            warnungen.append(f"BMA-Nummer ist Freitext: {roh[:60]}")

    # 3d) Kontakte
    anzahl_kontakte = 0
    for idx, k in enumerate(o.get("kontakte") or []):
        name = _kuerze(k.get("name"), 150)
        if not name:
            warnungen.append("Kontakt ohne Namen uebersprungen")
            continue
        telefone = [str(t).strip() for t in (k.get("telefone") or []) if str(t).strip()]
        db.add(ObjektKontakt(
            org_id=org_id,
            objekt_id=objekt.id,
            art=map_kontakt_art(k.get("art")),
            name=name,
            telefone_json=json.dumps(telefone, ensure_ascii=False) if telefone else None,
            email=_kuerze(k.get("email"), 200),
            erreichbarkeit=_kuerze(k.get("erreichbarkeit"), 200),
            sort=idx,
        ))
        anzahl_kontakte += 1
    detail["kontakte"] = anzahl_kontakte

    # 3e) Merkmal-Zuordnungen (Unique objekt_id+merkmal_id beachten)
    zugeordnet: set[int] = set()
    for m in o.get("merkmale") or []:
        ec_merkmal_id = merkmal_mapping.get(m.get("merkmal_eus_id"))
        if not ec_merkmal_id:
            warnungen.append(
                f"Merkmal {m.get('merkmal_eus_id')} nicht im Katalog-Mapping"
            )
            continue
        if ec_merkmal_id in zugeordnet:
            continue
        db.add(ObjektMerkmal(
            org_id=org_id,
            objekt_id=objekt.id,
            merkmal_id=ec_merkmal_id,
            hinweis=_kuerze(m.get("hinweis"), 300),
        ))
        zugeordnet.add(ec_merkmal_id)
    detail["merkmale"] = len(zugeordnet)

    # 3f) Dokumente: Phase 2 — nur zaehlen (Zaehler steckt in detail)
    return detail


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="EUS-Objekte (JSON-Export) in die Einsatzcockpit-Objektverwaltung importieren"
    )
    parser.add_argument("--input", required=True, help="Pfad zu eus_objekte_export.json")
    parser.add_argument("--org-id", required=True, type=int, help="Ziel-Organisation (fire_dept.id)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Alles ausfuehren, aber nichts committen")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Objekte ueberspringen, die es bereits gibt (name+strasse+hausnr)")
    args = parser.parse_args()

    prefix = "[DRY RUN] " if args.dry_run else ""

    eingabe = Path(args.input)
    if not eingabe.exists():
        print(f"FEHLER: Eingabedatei nicht gefunden: {eingabe}")
        return 1
    try:
        export = json.loads(eingabe.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"FEHLER: Eingabedatei kein gueltiges JSON: {exc}")
        return 1

    meta = export.get("meta") or {}
    print(f"{prefix}EUS-Import: Quelle={meta.get('source', '?')}, "
          f"exportiert={meta.get('exported_at', '?')}, "
          f"Objekte laut Export={((meta.get('counts') or {}).get('objekte', '?'))}")

    db = SessionLocal()
    set_tenant_context(db, None)  # CLI-Kontext: kein Request-Tenant (Fail-Closed-Bypass)
    stats = {
        "kategorien_neu": 0, "kategorien_vorhanden": 0,
        "merkmale_katalog_neu": 0, "merkmale_katalog_vorhanden": 0,
        "objekte_neu": 0, "objekte_uebersprungen": 0, "objekte_fehler": 0,
        "bma_angelegt": 0, "kontakte_angelegt": 0, "merkmal_zuordnungen": 0,
        "dokumente_phase2": 0,
    }
    log_eintraege: list[dict] = []
    fehler: list[dict] = []

    try:
        org = db.query(FireDept).filter(FireDept.id == args.org_id).first()
        if org is None:
            print(f"FEHLER: Organisation {args.org_id} existiert nicht")
            return 1
        print(f"{prefix}Ziel-Organisation: {org.name} (id={org.id})")

        # Schritt 1 + 2: Kataloge
        kategorie_mapping = importiere_kategorien(
            db, args.org_id, export.get("kategorien") or [], stats
        )
        merkmal_mapping = importiere_merkmale(
            db, args.org_id, export.get("merkmale") or [], stats
        )

        # Schritt 3: Objekte (jedes in eigenem try/except)
        nummern = Nummernvergabe(db, args.org_id)
        for o in export.get("objekte") or []:
            kennung = f"eus_id={o.get('eus_objekt_daten_id')} '{o.get('name', '?')}'"
            try:
                if args.skip_existing:
                    vorhanden = (
                        db.query(Objekt)
                        .filter(
                            Objekt.org_id == args.org_id,
                            Objekt.name == _kuerze(o.get("name"), 200),
                            Objekt.strasse == _kuerze(o.get("strasse"), 200),
                            Objekt.hausnummer == _kuerze(o.get("hausnummer"), 20),
                        )
                        .first()
                    )
                    if vorhanden:
                        print(f"{prefix}SKIP  {kennung} (bereits vorhanden als "
                              f"{vorhanden.anzeige_nummer})")
                        stats["objekte_uebersprungen"] += 1
                        log_eintraege.append({
                            "eus_objekt_daten_id": o.get("eus_objekt_daten_id"),
                            "name": o.get("name"),
                            "status": "uebersprungen",
                            "ec_objekt_id": vorhanden.id,
                        })
                        continue

                # SAVEPOINT je Objekt: ein Fehler verwirft nur dieses Objekt,
                # nicht die bereits vorbereiteten (Commit erfolgt erst am Ende)
                with db.begin_nested():
                    detail = importiere_objekt(
                        db, args.org_id, o, kategorie_mapping, merkmal_mapping, nummern
                    )
                stats["objekte_neu"] += 1
                stats["bma_angelegt"] += 1 if detail["bma"] else 0
                stats["kontakte_angelegt"] += detail["kontakte"]
                stats["merkmal_zuordnungen"] += detail["merkmale"]
                stats["dokumente_phase2"] += detail["dokumente_phase2"]
                log_eintraege.append(detail)
                warn = f"  ({len(detail['warnungen'])} Warnung/en)" if detail["warnungen"] else ""
                print(f"{prefix}OK    {kennung} → OBJ-{detail['nummer']:04d}{warn}")
            except Exception as exc:
                stats["objekte_fehler"] += 1
                fehler.append({
                    "eus_objekt_daten_id": o.get("eus_objekt_daten_id"),
                    "name": o.get("name"),
                    "fehler": str(exc)[:300],
                })
                log_eintraege.append({
                    "eus_objekt_daten_id": o.get("eus_objekt_daten_id"),
                    "name": o.get("name"),
                    "status": "fehler",
                    "fehler": str(exc)[:300],
                })
                print(f"{prefix}FEHLER {kennung}: {str(exc)[:160]}")

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()

    # Schritt 4: Zusammenfassung
    print(f"\n{prefix}Import abgeschlossen:")
    print(f"  Kategorien:  {stats['kategorien_neu']:>4} neu, "
          f"{stats['kategorien_vorhanden']:>4} vorhanden")
    print(f"  Merkmale:    {stats['merkmale_katalog_neu']:>4} neu, "
          f"{stats['merkmale_katalog_vorhanden']:>4} vorhanden")
    print(f"  Objekte:     {stats['objekte_neu']:>4} neu, "
          f"{stats['objekte_uebersprungen']:>4} übersprungen, "
          f"{stats['objekte_fehler']:>2} Fehler")
    print(f"  BMA-Blöcke:  {stats['bma_angelegt']:>4} angelegt")
    print(f"  Kontakte:    {stats['kontakte_angelegt']:>4} angelegt")
    print(f"  Merkmale:    {stats['merkmal_zuordnungen']:>4} zugeordnet")
    print(f"  Dokumente:   {stats['dokumente_phase2']:>4} (Phase 2 — noch nicht importiert)")

    if fehler:
        print("\nFehlerliste:")
        for f in fehler:
            print(f"  - eus_id={f['eus_objekt_daten_id']} '{f['name']}': {f['fehler']}")

    LOG_DATEI.write_text(json.dumps({
        "ausgefuehrt_am": datetime.now(UTC).isoformat(),
        "dry_run": args.dry_run,
        "skip_existing": args.skip_existing,
        "org_id": args.org_id,
        "input": str(eingabe),
        "stats": stats,
        "objekte": log_eintraege,
        "fehler": fehler,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{prefix}Detail-Log: {LOG_DATEI}")

    return 0 if not fehler else 2


if __name__ == "__main__":
    sys.exit(main())
