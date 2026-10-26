"""Org-bezogener Katalog aus vorhandenen lokalen Adressquellen."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import RLock
from time import monotonic

from sqlalchemy.orm import Session

from app.core.timezones import org_tz
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.objekt import Objekt
from app.models.planspiel import PlanspielAdresse
from app.models.wasserstelle import Wasserstelle

from .kontext import GemeindeInfo, ParserKontext
from .normalize import vergleichsform

_cache: dict[int, tuple[float, ParserKontext]] = {}
_lock = RLock()


def invalidiere_kontext(org_id: int) -> None:
    with _lock:
        _cache.pop(org_id, None)


def baue_kontext(db: Session, org_id: int) -> ParserKontext:
    """Snapshot fuer zehn Minuten; Aufrufer koennen ihn gefahrlos veraendern."""
    with _lock:
        entry = _cache.get(org_id)
        if entry and monotonic() - entry[0] < 600:
            return deepcopy(entry[1])
        kontext = ParserKontext()
        org = db.query(FireDept).filter(FireDept.id == org_id).one_or_none()
        kontext.zeitzone = org_tz(org).key

        def adresse(ort: str | None, strasse: str | None, objekt: str | None = None) -> None:
            if not ort or not ort.strip():
                return
            name = ort.strip()
            gemeinde = kontext.gemeinden.setdefault(vergleichsform(name), GemeindeInfo(name))
            if strasse and strasse.strip():
                gemeinde.strassen[vergleichsform(strasse.strip())] = strasse.strip()
            if objekt and objekt.strip():
                gemeinde.objekte[vergleichsform(objekt.strip())] = objekt.strip()

        if org:
            adresse(org.city, org.street)
        for ort, strasse, name in db.query(Objekt.ort, Objekt.strasse, Objekt.name).filter(
            Objekt.org_id == org_id, Objekt.entwurf_von_id.is_(None),
        ):
            adresse(ort, strasse, name)
        # PlanspielAdresse enthaelt auch die vorhandenen OSM-/Strassenpunkt-Importe.
        for ort, strasse, bezeichnung in db.query(
            PlanspielAdresse.ort, PlanspielAdresse.strasse, PlanspielAdresse.bezeichnung,
        ).filter(PlanspielAdresse.org_id == org_id, PlanspielAdresse.aktiv.is_(True)):
            adresse(ort, strasse, bezeichnung)
        seit = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=731)
        for ort, strasse in db.query(Incident.address_city, Incident.address_street).filter(
            Incident.primary_org_id == org_id, Incident.started_at >= seit,
        ).distinct().order_by(Incident.address_city, Incident.address_street).limit(5000):
            adresse(ort, strasse)
        # Wasserstellen besitzen keine Adressspalten. Nur ihre Bezeichnung wird
        # als benannter Ort in der bekannten Heimatgemeinde verwendet.
        for (name,) in db.query(Wasserstelle.bezeichnung).filter(
            Wasserstelle.org_id == org_id, Wasserstelle.aktiv.is_(True),
        ):
            adresse(org.city if org else None, None, name)
        _cache[org_id] = (monotonic(), deepcopy(kontext))
        return kontext
