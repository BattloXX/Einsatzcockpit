"""Regressionstests PR5 (STAB-4): Schadenmeldung (Mail/Teams) darf die
Fahrtenbuch-Request-Antwort nicht mehr blockieren."""
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import FahrtErfassungsweg, FahrtKategorie, Fahrtzweck
from app.models.master import VehicleMaster
from app.services import schaden_service
from app.services.fahrtenbuch_service import erstelle_fahrt
from app.services.teams_service import post_teams_karte


def test_post_teams_karte_is_async():
    """post_teams_karte() muss async sein (httpx.AsyncClient statt sync httpx.post),
    sonst blockiert ein einziger synchroner Aufruf den gesamten Event-Loop bis zu 10s."""
    assert inspect.iscoroutinefunction(post_teams_karte)


@pytest.mark.asyncio
async def test_post_teams_karte_invalid_url_returns_false_without_network():
    ok = await post_teams_karte("not-https", "Titel", "Text")
    assert ok is False


@pytest.fixture()
def db_session(setup_db):
    from tests.conftest import TestingSession
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


@pytest.fixture()
def fahrt_mit_schaden(db_session):
    from app.models.master import FireDept
    org = db_session.query(FireDept).first()
    fz = (
        db_session.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == org.id)
        .first()
    )
    if not fz:
        fz = VehicleMaster(dept_id=org.id, code="TEST-FZ2", name="Testfahrzeug2", type="Test",
                            display_order=98)
        db_session.add(fz)
        db_session.flush()
    zweck = db_session.query(Fahrtzweck).filter(Fahrtzweck.org_id == org.id).first()
    if not zweck:
        zweck = Fahrtzweck(org_id=org.id, name="Testübung", kategorie=FahrtKategorie.uebung)
        db_session.add(zweck)
        db_session.flush()
    daten = {
        "org_id": org.id,
        "fahrzeug_id": fz.id,
        "zweck_id": zweck.id,
        "maschinist_name": "Max Mustermann",
        "km_stand_neu": (fz.km_aktuell or 0) + 10,
        "erfasst_via": FahrtErfassungsweg.web,
        "schaden_vorhanden": True,
        "schaden_betriebsfaehig": True,
        "schaden_beschreibung": "Testschaden",
    }
    fahrt = erstelle_fahrt(daten, db_session)
    db_session.commit()
    return fahrt.id


@pytest.mark.asyncio
async def test_melde_schaden_background_swallows_errors(fahrt_mit_schaden):
    """Ein Fehler in melde_schaden() darf aus dem BackgroundTask nicht propagieren
    (sonst würde ein toter Mail-/Teams-Server einen Log-Sturm o.ä. auslösen)."""
    with patch.object(schaden_service, "melde_schaden", AsyncMock(side_effect=RuntimeError("boom"))):
        await schaden_service.melde_schaden_background(fahrt_mit_schaden, base_url="http://test")


@pytest.mark.asyncio
async def test_melde_schaden_background_unknown_fahrt_id_noop():
    await schaden_service.melde_schaden_background(-1, base_url="http://test")


# ── Mehrere Mail-Empfänger ────────────────────────────────────────────────────

def test_normalize_email_list_trennt_und_bereinigt():
    from app.services.mail_service import normalize_email_list
    # Komma, Semikolon, Leerzeichen und Zeilenumbruch werden als Trenner akzeptiert
    assert normalize_email_list("a@x.at; b@y.at  c@z.at") == "a@x.at, b@y.at, c@z.at"
    assert normalize_email_list("a@x.at,\nb@y.at") == "a@x.at, b@y.at"


def test_normalize_email_list_verwirft_ungueltige_und_duplikate():
    from app.services.mail_service import normalize_email_list
    assert normalize_email_list("gut@x.at, kaputt, a@x.at, GUT@x.at") == "gut@x.at, a@x.at"
    assert normalize_email_list("") == ""
    assert normalize_email_list(None) == ""
    assert normalize_email_list("nur-müll ; ,, ") == ""


@pytest.mark.asyncio
async def test_melde_schaden_sendet_an_mehrere_adressen(db_session, fahrt_mit_schaden):
    """Ein Fahrzeug mit mehreren (gemischt getrennten) Schaden-Adressen erzeugt einen
    komma-separierten To-Header, sodass alle Empfänger die Mail erhalten."""
    from app.models.fahrtenbuch import Fahrt
    fahrt = db_session.get(Fahrt, fahrt_mit_schaden)
    fz = (
        db_session.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrt.fahrzeug_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    fz.schaden_mail_override = "a@x.at; b@y.at  c@z.at"
    db_session.flush()

    sent: dict = {}

    async def fake_send(msg, cfg):
        sent["to"] = msg["To"]

    with patch("app.services.mail_service._send", side_effect=fake_send):
        await schaden_service.melde_schaden(fahrt, db_session, base_url="http://test")

    assert sent.get("to") == "a@x.at, b@y.at, c@z.at"
