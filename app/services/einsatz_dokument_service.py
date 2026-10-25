"""Explizite Dokumentübernahme mit unabhängiger Einsatzfassung und Freigaben."""

import hashlib
import io
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role
from app.core.queries import visible_incidents_q
from app.models.einsatz_dokument import EinsatzDokument, EinsatzDokumentEmpfaenger, EinsatzDokumentFassung
from app.models.incident import Incident, Task
from app.models.katplan import KatplanAnhang, KatplanDokument, KatplanPlan, KatplanVersion, KatplanVersionElement
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident
from app.models.nachschlagewerk import RettungsdatenblattCache
from app.models.user import User
from app.services.dokumentdruck_service import _bild_pdf, quell_datei
from app.services.dokumentviewer_service import pdf_auszug, seiten_auswahl
from app.services.katplan_anhang_service import anhaenge_aus_version
from app.services.katplan_zugriff import sieht_vertraulich


def vollzugang(request: Request, user: User | None) -> User:
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    # Ein allgemeiner Board-/Gerätetoken darf niemals dessen Aussteller vertreten.
    if (not user.active or user.org_id is None or getattr(request.state, "is_device", False)
            or getattr(request.state, "is_qr", False) or getattr(request.state, "qr_incident_id", None)
            or getattr(request.state, "qr_lage_id", None)):
        raise HTTPException(403, "Bitte einen persönlichen Zugang verwenden.")
    if not has_role(user, "incident_leader", "admin", "org_admin", "recorder", "readonly"):
        raise HTTPException(403, "Keine Einsatzberechtigung.")
    return user


def ziel(db: Session, user: User, art: str, ident: int, *, schreiben: bool = False):
    if not 0 < ident <= 9223372036854775807:
        raise HTTPException(404, "Einsatz nicht gefunden.")
    if art == "einsatz":
        query = visible_incidents_q(db, user).filter(Incident.id == ident)
    elif art == "lage":
        query = db.query(MajorIncident).filter_by(id=ident, org_id=user.org_id)
    else:
        raise HTTPException(404, "Einsatz nicht gefunden.")
    obj = (query.with_for_update() if schreiben else query).first()
    if obj is None:
        raise HTTPException(404, "Einsatz nicht gefunden.")
    if schreiben:
        if (not has_role(user, "incident_leader", "admin", "org_admin", "recorder")
                or getattr(user, "gsl_nur_lesen", False)):
            raise HTTPException(403, "Keine Berechtigung zur Dokumentenverwaltung.")
        if obj.status != "active":
            raise HTTPException(409, "Einsatz ist abgeschlossen oder nicht aktiv.")
    return obj


def ziel_filter(art: str, ident: int):
    return EinsatzDokument.incident_id == ident if art == "einsatz" else EinsatzDokument.lage_id == ident


def dokument(db: Session, user: User, ident: int, *, schreiben: bool = False) -> EinsatzDokument:
    if not 0 < ident <= 9223372036854775807:
        raise HTTPException(404, "Dokument nicht gefunden.")
    query = db.query(EinsatzDokument).filter_by(id=ident, org_id=user.org_id, entfernt_am=None)
    obj = (query.with_for_update() if schreiben else query).first()
    if obj is None or (obj.vertraulich and not sieht_vertraulich(user)):
        raise HTTPException(404, "Dokument nicht gefunden.")
    art, ziel_id = dokument_ziel(obj)
    ziel(db, user, art, ziel_id, schreiben=schreiben)
    return obj


def dokument_ziel(obj: EinsatzDokument) -> tuple[str, int]:
    if obj.incident_id is not None:
        return "einsatz", obj.incident_id
    assert obj.lage_id is not None
    return "lage", obj.lage_id


def quellreferenz(db: Session, user: User, url: str) -> tuple[str, dict]:
    """Nur bekannte Quellen; auch Verwalter übernehmen ausschließlich Freigaben."""
    from app.services.dokumentdruck_service import _quell_request

    _quell_request(user, url)
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    if (set(params) - {"version_id", "seiten", "auswahl", "auszug", "gliederung", "akteur"}
            or any(len(v) != 1 for v in params.values())):
        raise HTTPException(422, "Ungültige Dokumentparameter.")
    path = parsed.path
    if match := re.fullmatch(r"/nachschlagewerk-cache/rettungskarten/([1-9]\d*)/original.pdf", path):
        if len(match[1]) > 18:
            raise HTTPException(422, "Ungültige Dokumentreferenz.")
        karte = db.get(RettungsdatenblattCache, int(match[1]))
        if karte is None:
            raise HTTPException(404, "Rettungskarte nicht gefunden.")
        return url, {"titel": karte.anzeige_name, "quelle": "Rettungskarten", "typ": "Rettungskarte",
                     "quellversion": karte.sha256, "freigabe": "Veröffentlichte Rettungskarte",
                     "vertraulich": False, "original_url": path}
    quelle = None
    anhang = None
    generiert = False
    if match := re.fullmatch(r"/plaene/anhang/([\w-]+)", path):
        anhang = db.query(KatplanAnhang).filter_by(org_id=user.org_id, uuid=match[1]).first()
        plan_id = anhang.plan_id if anhang else None
    elif match := re.fullmatch(r"/plaene/quelle/([\w-]+)/(?:original|seite/[1-9]\d*/(?:pdf|bild))", path):
        quelle = db.query(KatplanDokument).filter_by(org_id=user.org_id, uuid=match[1]).first()
        plan_id = quelle.plan_id if quelle else None
    elif match := re.fullmatch(r"/plaene/([1-9]\d*)/pdf", path):
        if len(match[1]) > 18:
            raise HTTPException(422, "Ungültige Dokumentreferenz.")
        plan_id = int(match[1])
        generiert = True
    else:
        raise HTTPException(422, "Bitte einen freigegebenen Anhang, eine Planquelle oder Rettungskarte wählen.")
    plan = db.query(KatplanPlan).filter_by(id=plan_id, org_id=user.org_id).first()
    if plan is None or plan.status == "archiviert":
        raise HTTPException(404, "Quelle nicht verfügbar.")
    vid = params.get("version_id", [str(plan.aktuelle_version_id or "")])[0]
    if not vid.isdigit() or len(vid) > 18:
        raise HTTPException(409, "Bitte zuerst eine freigegebene Quellversion wählen.")
    version = db.query(KatplanVersion).filter_by(id=int(vid), plan_id=plan.id, org_id=user.org_id).first()
    if version is None:
        raise HTTPException(404, "Freigabe nicht gefunden.")
    kopf = json.loads(version.plan_json)
    vertraulich = False
    quellversion: int | str | None = None
    objekt_seiten = None
    if generiert:
        titel = kopf["titel"]
        typ = "Plan-PDF"
        quellversion = version.versionsnummer
        vertraulich = sieht_vertraulich(user) and (
            any(a.get("vertraulich") for a in anhaenge_aus_version(db, plan, version.id))
            or db.query(KatplanVersionElement.id).filter_by(
                org_id=user.org_id, version_id=version.id, vertraulich=True).first() is not None)
    elif anhang:
        ref = next((a for a in anhaenge_aus_version(db, plan, version.id) if a["uuid"] == anhang.uuid), None)
        if ref is None:
            raise HTTPException(404, "Dokument gehört nicht zu dieser Freigabe.")
        titel = ref.get("beschriftung") or ref.get("dateiname") or "Objektdokument"
        vertraulich = ref.get("vertraulich", False)
        quellversion = version.versionsnummer
        typ = "Bild" if (ref.get("mime") or "").startswith("image/") else "PDF"
        if ref["art"] == "objekt_dokument":
            from app.models.objekt import ObjektDokument

            typ = "Objektdokument"
            objekt = db.query(ObjektDokument).filter_by(id=ref.get("objekt_dokument_id"), org_id=user.org_id).first()
            if objekt is None:
                raise HTTPException(404, "Objektdokument nicht gefunden.")
            quellversion = objekt.versionsnummer
            objekt_seiten = ref.get("seiten")
    else:
        assert quelle is not None
        ref = next((q for q in kopf.get("quellen", []) if q["id"] == quelle.id), None)
        elemente = db.query(KatplanVersionElement).filter_by(org_id=user.org_id, version_id=version.id,
                                                            quelle_dokument_id=quelle.id).all()
        if ref is None and not elemente:
            raise HTTPException(404, "Dokument gehört nicht zu dieser Freigabe.")
        # Eine Quelle, die ausschließlich über vertrauliche Maßnahmen zugänglich
        # ist, darf nicht durch einen Original-Link deklassifiziert werden.
        vertraulich = ref is None and all(e.vertraulich for e in elemente)
        titel = ref["titel"] if ref else quelle.titel
        quellversion = ref["versionsnummer"] if ref else quelle.versionsnummer
        typ = "Planquelle"
    if vertraulich and not sieht_vertraulich(user):
        raise HTTPException(404, "Dokument nicht verfügbar.")
    params["version_id"] = [str(version.id)]
    pinned = path + "?" + urlencode({k: v[0] for k, v in params.items()})
    return pinned, {"titel": titel, "quelle": kopf.get("titel", plan.titel), "typ": typ,
                    "plan_id": plan.id, "plan_uuid": plan.uuid, "version_id": version.id,
                    "freigabe_nummer": version.versionsnummer,
                    "freigabe": f"v{version.versionsnummer}", "freigabe_hash": version.inhalt_hash,
                    "quellversion": quellversion, "vertraulich": vertraulich, "original_url": pinned,
                    "original_uuid": anhang.uuid if anhang else quelle.uuid if quelle else plan.uuid,
                    "objekt_seiten": objekt_seiten}


def quell_link(db: Session, user: User, obj: EinsatzDokument) -> str:
    """Historische IDs bleiben im Beleg; Live-Links nach Restore über UUID auflösen."""
    meta = json.loads(obj.quelle_json)
    if not meta.get("plan_uuid"):
        return obj.quell_url
    plan = db.query(KatplanPlan).filter_by(org_id=user.org_id, uuid=meta["plan_uuid"]).first()
    if plan is None:
        raise HTTPException(404, "Die ursprüngliche Quelle ist nicht mehr verfügbar. "
                            "Die Einsatzfassung bleibt erhalten.")
    version = db.query(KatplanVersion).filter_by(org_id=user.org_id, plan_id=plan.id,
                                                versionsnummer=meta["freigabe_nummer"]).first()
    if version is None:
        raise HTTPException(404, "Die ursprüngliche Freigabe ist nicht mehr verfügbar.")
    url = urlsplit(obj.quell_url)
    path = re.sub(r"^/plaene/[1-9]\d*/pdf$", f"/plaene/{plan.id}/pdf", url.path)
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    params["version_id"] = str(version.id)
    return path + "?" + urlencode(params)


def zuordnung(db: Session, user: User, art: str, ident: int, auftrag_id: int | None,
              einheiten: list[int], auftrag_freigeben: bool) -> tuple[int | None, int | None]:
    if (auftrag_id is not None and not 0 < auftrag_id <= 9223372036854775807
            or any(not 0 < e <= 9223372036854775807 for e in einheiten)):
        raise HTTPException(422, "Ungültiger Auftrag oder Empfänger.")
    if art == "einsatz":
        if einheiten or auftrag_freigeben:
            raise HTTPException(422, "Einheitenfreigaben benötigen eine Großschadenslage.")
        if auftrag_id and not db.query(Task.id).filter_by(id=auftrag_id, incident_id=ident).first():
            raise HTTPException(404, "Auftrag nicht gefunden.")
        return auftrag_id, None
    if auftrag_id and not db.query(IncidentSite.id).filter_by(
            id=auftrag_id, major_incident_id=ident, org_id=user.org_id).first():
        raise HTTPException(404, "Auftrag nicht gefunden.")
    if auftrag_freigeben and not auftrag_id:
        raise HTTPException(422, "Bitte zuerst einen Auftrag zuordnen.")
    vorhanden = {e.id for e in db.query(LageEinheit).filter(LageEinheit.lage_id == ident,
                                                         LageEinheit.id.in_(einheiten))}
    if vorhanden != set(einheiten):
        raise HTTPException(404, "Einheit nicht gefunden.")
    return None, auftrag_id


def audit(db: Session, user: User, obj: EinsatzDokument, aktion: str, payload: dict) -> None:
    write_audit(db, "einsatz.dokument." + aktion, org_id=user.org_id, user_id=user.id,
                incident_id=obj.incident_id, entity_type="einsatz_dokument", entity_id=obj.id,
                payload={"lage_id": obj.lage_id, "revision": obj.revision, **payload})


def uebernehmen(db: Session, user: User, daten: dict) -> list[EinsatzDokument]:
    if not user.active or user.org_id is None:
        raise HTTPException(403, "Keine Organisation zugeordnet.")
    art, ident = daten["art"], daten["einsatz_id"]
    ziel(db, user, art, ident, schreiben=True)
    task_id, site_id = zuordnung(db, user, art, ident, daten["auftrag_id"], daten["einheit_ids"],
                                daten["auftrag_freigeben"])
    vorbereitet = []
    gesamt = 0
    # PyMySQL maskiert Binaerdaten im SQL-Paket; vor dem Schreiben die tatsächliche
    # Servergrenze prüfen, damit eine große Fassung keine Verbindung abbricht.
    bind = db.get_bind()
    paketlimit = (int(db.execute(text("SELECT @@max_allowed_packet")).scalar_one())
                  if bind.dialect.name in {"mysql", "mariadb"} else None)
    for ref in daten["documents"]:
        url, meta = quellreferenz(db, user, ref["url"])
        if (meta["vertraulich"] and (daten["einheit_ids"] or daten["auftrag_freigeben"])
                and not daten["vertraulich_bestaetigt"]):
            raise HTTPException(422, "Vertrauliche Unterlagen nur nach ausdrücklicher Bestätigung freigeben.")
        data, mime, _ = quell_datei(db, user, url)
        meta["sha256_quelle"] = hashlib.sha256(data).hexdigest()
        gesamt += len(data)
        if gesamt > 150 * 1024 * 1024:
            raise HTTPException(413, "Dokumentauswahl ist zu groß.")
        if mime.startswith("image/"):
            data = _bild_pdf(data)
        elif mime != "application/pdf":
            raise HTTPException(422, "Nur PDF und Bilder können übernommen werden.")
        from pypdf import PdfReader
        try:
            seiten = seiten_auswahl(ref["pages"], len(PdfReader(io.BytesIO(data)).pages))
        except Exception as exc:
            raise HTTPException(422, "Dokument oder Seitenauswahl ungültig.") from exc
        meta["ausgangsreferenz"] = ref["url"]
        # Anzeige der Originalseiten auch bei verschachtelten Viewer-Auszügen.
        originalseiten = None
        params = parse_qs(urlsplit(url).query)
        if meta.get("objekt_seiten"):
            originalseiten = seiten_auswahl(meta["objekt_seiten"], 1000000)
        if params.get("seiten"):
            auswahl = seiten_auswahl(params["seiten"][0], len(originalseiten) if originalseiten else 1000000)
            originalseiten = [originalseiten[n - 1] for n in auswahl] if originalseiten else auswahl
        if params.get("auswahl"):
            auswahl = seiten_auswahl(params["auswahl"][0], len(originalseiten) if originalseiten else 1000000)
            originalseiten = [originalseiten[n - 1] for n in auswahl] if originalseiten else auswahl
        if match := re.search(r"/seite/([1-9]\d*)/(?:pdf|bild)$", urlsplit(url).path):
            originalseiten = [int(match[1])]
        meta["originalseiten"] = [originalseiten[n - 1] for n in seiten] if originalseiten else seiten
        data = pdf_auszug(data, ",".join(map(str, seiten)))
        if paketlimit is not None and 2 * len(data) + 65536 > paketlimit:
            raise HTTPException(413, "Die Einsatzfassung überschreitet die Datenbank-Paketgrenze. "
                                "Bitte weniger Seiten auswählen oder die Administration verständigen.")
        vorbereitet.append((data, url, meta, seiten))
    if sum(len(p[3]) for p in vorbereitet) > 2000:
        raise HTTPException(422, "Höchstens 2000 Seiten pro Übernahme.")
    result = []
    for data, url, meta, seiten in vorbereitet:
        sha = hashlib.sha256(data).hexdigest()
        fassung = db.query(EinsatzDokumentFassung).filter_by(org_id=user.org_id, sha256=sha).first()
        if fassung is None:
            try:
                with db.begin_nested():
                    fassung = EinsatzDokumentFassung(org_id=user.org_id, sha256=sha, inhalt=data)
                    db.add(fassung)
                    db.flush()
                    from app.services.storage_service import reserve_storage

                    reserve_storage(db, user.org_id, len(data))
            except IntegrityError:
                fassung = db.query(EinsatzDokumentFassung).filter_by(org_id=user.org_id, sha256=sha).one()
        obj = EinsatzDokument(org_id=user.org_id, incident_id=ident if art == "einsatz" else None,
            lage_id=ident if art == "lage" else None, fassung_id=fassung.id, titel=meta["titel"][:300],
            quell_url=url, quelle_json=json.dumps(meta, ensure_ascii=False),
            seiten=",".join(map(str, meta["originalseiten"])),
            seitenzahl=len(seiten), vertraulich=meta["vertraulich"], uebernommen_von_id=user.id,
            task_id=task_id, site_id=site_id, auftrag_freigegeben=daten["auftrag_freigeben"])
        obj.empfaenger = [EinsatzDokumentEmpfaenger(org_id=user.org_id, einheit_id=e)
                          for e in sorted(set(daten["einheit_ids"]))]
        db.add(obj)
        db.flush()
        audit(db, user, obj, "uebernommen", {"quelle": meta, "seiten": obj.seiten, "sha256": sha,
            "auftrag_id": daten["auftrag_id"], "einheit_ids": daten["einheit_ids"],
            "auftrag_freigegeben": daten["auftrag_freigeben"],
            "vertraulich_bestaetigt": daten["vertraulich_bestaetigt"]})
        result.append(obj)
    return result


def fuer_einheit(db: Session, ctx):
    eigene_auftraege = select(EinheitSiteDispatch.site_id).where(
        EinheitSiteDispatch.einheit_id == ctx.einheit.id, EinheitSiteDispatch.withdrawn_at.is_(None),
        EinheitSiteDispatch.beendet_at.is_(None))
    explizit = select(EinsatzDokumentEmpfaenger.dokument_id).where(
        EinsatzDokumentEmpfaenger.org_id == ctx.org_id, EinsatzDokumentEmpfaenger.einheit_id == ctx.einheit.id)
    return db.query(EinsatzDokument).filter(
        EinsatzDokument.org_id == ctx.org_id, EinsatzDokument.lage_id == ctx.lage.id,
        EinsatzDokument.entfernt_am.is_(None), or_(EinsatzDokument.id.in_(explizit),
        (EinsatzDokument.auftrag_freigegeben.is_(True) & EinsatzDokument.site_id.in_(eigene_auftraege))))


def pdf_daten(db: Session, obj: EinsatzDokument) -> bytes:
    fassung = db.query(EinsatzDokumentFassung).filter_by(id=obj.fassung_id, org_id=obj.org_id).one()
    data = fassung.inhalt
    if hashlib.sha256(data).hexdigest() != fassung.sha256:
        raise HTTPException(409, "Dokumentfassung ist beschädigt. Bitte Administration verständigen.")
    return data


def endgueltig_entfernen(db: Session, incident_id: int) -> None:
    """Nur beim bestehenden, ausdrücklich bestätigten endgültigen Einsatzlöschen."""
    # Der aufrufende Archivcontroller hat das endgültige Löschen autorisiert.
    # Bei Kollaboration können Belege mehrerer Organisationen am Einsatz hängen.
    docs = db.query(EinsatzDokument).filter_by(incident_id=incident_id).execution_options(
        include_all_tenants=True).all()
    fassungen = {d.fassung_id for d in docs}
    for doc in docs:
        db.delete(doc)
    db.flush()
    for fid in sorted(fassungen):
        if not db.query(EinsatzDokument.id).filter_by(fassung_id=fid).execution_options(
                include_all_tenants=True).first():
            from sqlalchemy import func

            from app.services.storage_service import release_storage

            blob = db.query(EinsatzDokumentFassung.org_id, func.length(EinsatzDokumentFassung.inhalt)).filter_by(
                id=fid).execution_options(include_all_tenants=True).with_for_update().one()
            release_storage(db, blob[0], blob[1])
            db.query(EinsatzDokumentFassung).filter_by(id=fid).delete(synchronize_session=False)
