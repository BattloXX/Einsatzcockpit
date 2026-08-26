"""Einsatzinfo an Objektkontakte: Versand, Gates, Idempotenz und Datenpflege."""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    Objekt,
    ObjektEinsatz,
    ObjektKontakt,
    ObjektKontaktBenachrichtigung,
)
from app.services.objekt_kontakt_notify import (
    default_kontakt_info_betreff,
    default_kontakt_info_template,
    dispatch_objekt_einsatzinfo,
    loese_betreff,
    loese_template,
    sammle_ziele,
    stichwort_erlaubt,
)
from app.services.sms_dispatch_service import render_template
from app.services.objekt_service import telefone_aus_form, telefon_normalisiert


@pytest.fixture()
def versand(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org = FireDept(slug="notify", name="FF Test", color="#ff0000", timezone="Europe/Vienna")
    db.add(org)
    db.flush()
    settings = OrgSettings(org_id=org.id, objekt_module_enabled=True)
    db.add(settings)
    objekt = Objekt(
        org_id=org.id, nummer=7, name="Werk", strasse="Werkstraße", hausnummer="1",
        ort="Testort", status="freigegeben",
    )
    db.add(objekt)
    db.flush()
    kontakt = ObjektKontakt(
        org_id=org.id, objekt_id=objekt.id, name="Frau Kontakt", art="betreiber",
        email="kontakt@example.at", telefone_json=json.dumps([
            {"nummer": "+43660111", "label": None, "sms": True}
        ]), benachrichtigung_mail=True,
    )
    incident = Incident(
        primary_org_id=org.id, alarm_type_code="B2", is_exercise=False,
        report_text="Brandmelder", reason="Brand", lis_operation_number="f26",
    )
    db.add_all([kontakt, incident])
    db.flush()
    link = ObjektEinsatz(
        org_id=org.id, objekt_id=objekt.id, incident_id=incident.id,
        quelle="manuell", status="bestaetigt",
    )
    db.add(link)
    db.commit()

    import app.services.objekt_kontakt_notify as notify
    import app.services.objekt_service as objekt_service
    monkeypatch.setattr(notify, "SessionLocal", Session)
    monkeypatch.setattr(objekt_service, "objekt_effective_enabled", lambda org_id, db: True)
    mails, sms = [], []

    async def deliver(db, org_id, msg, smtp_cfg):
        mails.append(msg)

    async def send_sms(org_id, nummer, text, ctx=None):
        sms.append((nummer, text))
        return True

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(notify, "send_sms", send_sms)
    monkeypatch.setattr(notify, "sms_available", lambda org_id, db=None: True)
    monkeypatch.setattr(notify, "resolve_sms_config", lambda org_id, db=None: object())
    yield db, org, settings, objekt, kontakt, incident, link, mails, sms, notify
    db.close()
    Base.metadata.drop_all(engine)


def _run(incident_id, **kw):
    return asyncio.run(dispatch_objekt_einsatzinfo(incident_id, **kw))


@pytest.mark.parametrize("raw, erwartet", [
    ('["Mobil beruflich: +43 664 1"]', [{"nummer": "+43 664 1", "label": "Mobil beruflich", "sms": False}]),
    ('[{"nummer":"+431","label":"Büro","sms":true}]', [{"nummer": "+431", "label": "Büro", "sms": True}]),
    ('["Notruf: 24/7", {"nummer":"2"}]', [{"nummer": "Notruf: 24/7", "label": None, "sms": False}, {"nummer": "2", "label": None, "sms": False}]),
    ('{kaputt', []),
])
def test_telefone_eintraege_legacy_und_v2(raw, erwartet):
    kontakt = ObjektKontakt(telefone_json=raw)
    assert kontakt.telefone_eintraege == erwartet
    assert kontakt.telefone == [f'{e["label"]}: {e["nummer"]}' if e["label"] else e["nummer"] for e in erwartet]


@pytest.mark.parametrize("wert", ["Notruf: 24/7", "Stock: 2. OG", "Firma: Zentrale: +43 555 123", "Telefon: DW 123"])
def test_unbekannte_legacy_praefixe_bleiben_unzerlegt(wert):
    assert ObjektKontakt(telefone_json=json.dumps([wert])).telefone_eintraege[0]["nummer"] == wert


def test_telefon_normalisiert_und_formularindizes():
    assert telefon_normalisiert("+43 664 88162932") == telefon_normalisiert("0043-664/881 629 32")
    assert telefon_normalisiert("+43 664 1") != telefon_normalisiert("+43 664 88162932")
    wert = telefone_aus_form(["", "+431", "+432"], ["", "A", "B"], ["1", "-1", "x", "99", "1"])
    assert json.loads(wert) == [
        {"nummer": "+431", "label": "A", "sms": True},
        {"nummer": "+432", "label": "B", "sms": False},
    ]
    with pytest.raises(ValueError):
        telefone_aus_form(["1"], [], [])


def test_01_unbekannter_platzhalter_ist_leer():
    assert render_template("A{x}B", {}) == "AB"


def test_02_vorlagen_kaskade(versand):
    _, _, settings, objekt, *_ = versand
    assert loese_betreff(objekt, settings) == default_kontakt_info_betreff()
    assert loese_template(objekt, settings) == default_kontakt_info_template()
    settings.objekt_kontakt_info_betreff = "Org"
    settings.objekt_kontakt_info_template = "Org Text"
    assert loese_betreff(objekt, settings) == "Org"
    assert loese_template(objekt, settings) == "Org Text"
    objekt.kontakt_info_betreff = "Objekt"
    objekt.kontakt_info_template = "Objekt Text"
    assert loese_betreff(objekt, settings) == "Objekt"
    assert loese_template(objekt, settings) == "Objekt Text"


def test_03_deaktiviert_ohne_log(versand):
    db, _, _, _, kontakt, incident, _, mails, sms, _ = versand
    kontakt.benachrichtigung_mail = False
    kontakt.telefone_json = None
    db.commit()
    assert _run(incident.id)["gesendet"] == 0
    assert not mails and not sms and db.query(ObjektKontaktBenachrichtigung).count() == 0


def test_04_uebungsfilter_und_praefixe(versand):
    db, _, _, objekt, _, incident, _, mails, sms, _ = versand
    incident.is_exercise = True
    db.commit()
    _run(incident.id)
    assert not mails and not sms
    objekt.kontakt_info_uebung = True
    db.commit()
    _run(incident.id)
    assert mails[0]["Subject"].startswith("[ÜBUNG]")
    assert sms[0][1].startswith("[UEBUNG]")


def test_05_nur_bestaetigte_verknuepfung(versand):
    db, _, _, _, _, incident, link, mails, sms, _ = versand
    link.status = "vorschlag"
    db.commit()
    _run(incident.id)
    assert not mails and not sms
    link.status = "bestaetigt"
    db.commit()
    assert _run(incident.id)["gesendet"] == 2


def test_06_stichwortfilter_case_und_leerzeichen(versand):
    _, _, _, objekt, *_ = versand
    objekt.kontakt_info_stichworte = " B1, b2 "
    assert not stichwort_erlaubt(objekt, "T1")
    assert stichwort_erlaubt(objekt, "B2")


def test_07_ungueltige_mail_ohne_mail_log(versand):
    db, _, _, _, kontakt, incident, _, mails, _, _ = versand
    kontakt.email = "ungueltig"
    kontakt.telefone_json = None
    db.commit()
    _run(incident.id)
    assert not mails and db.query(ObjektKontaktBenachrichtigung).count() == 0


def test_08_sms_freigabe_je_nummer(versand):
    _, _, _, _, kontakt, *_ = versand
    kontakt.telefone_json = json.dumps([
        {"nummer": "+43 660 111", "label": "Privat", "sms": False},
        {"nummer": "0043-660/999", "label": "Mobil", "sms": True},
    ])
    assert [(kanal, ziel) for _, kanal, ziel in sammle_ziele(kontakt.objekt)] == [
        ("mail", "kontakt@example.at"), ("sms", "+43660999")
    ]


def test_09_10_mail_sms_und_log(versand):
    db, _, _, objekt, _, incident, _, mails, sms, _ = versand
    objekt.kontakt_info_betreff = "Alarm {objekt}"
    objekt.kontakt_info_template = "Hallo {kontakt}: {stichwort} {adresse}"
    db.commit()
    result = _run(incident.id)
    assert result == {"gesendet": 2, "fehler": 0, "uebersprungen": 0}
    assert len(mails) == len(sms) == 1
    assert mails[0]["To"] == "kontakt@example.at" and mails[0]["Subject"] == "Alarm Werk"
    assert sms[0] == ("+43660111", "Hallo Frau Kontakt: B2 Werkstraße 1, Testort")
    assert {x.status for x in db.query(ObjektKontaktBenachrichtigung).all()} == {"gesendet"}


def test_11_idempotenz(versand):
    db, _, _, _, kontakt, incident, _, mails, sms, _ = versand
    kontakt.telefone_json = None
    db.commit()
    _run(incident.id)
    _run(incident.id)
    assert len(mails) == 1 and not sms
    assert db.query(ObjektKontaktBenachrichtigung).count() == 1


def test_12_retry_verwendet_dieselbe_zeile(versand, monkeypatch):
    db, _, _, _, kontakt, incident, _, mails, _, notify = versand
    kontakt.telefone_json = None
    db.commit()

    async def kaputt(*args, **kwargs):
        raise RuntimeError("SMTP kaputt")

    monkeypatch.setattr(notify, "deliver", kaputt)
    _run(incident.id)
    zeile = db.query(ObjektKontaktBenachrichtigung).one()
    assert zeile.status == "fehler" and zeile.fehlertext == "SMTP kaputt"

    async def ok(db, org_id, msg, smtp_cfg):
        mails.append(msg)

    monkeypatch.setattr(notify, "deliver", ok)
    _run(incident.id, force=True)
    db.expire_all()
    assert db.query(ObjektKontaktBenachrichtigung).one().status == "gesendet"
    assert db.query(ObjektKontaktBenachrichtigung).count() == 1


def test_13_sms_nicht_verfuegbar_mail_laeuft(versand, monkeypatch):
    db, _, _, _, _, incident, _, mails, sms, notify = versand
    monkeypatch.setattr(notify, "sms_available", lambda org_id, db=None: False)
    _run(incident.id)
    assert len(mails) == 1 and not sms
    assert [x.kanal for x in db.query(ObjektKontaktBenachrichtigung).all()] == ["mail"]


def test_zwei_freigegebene_nummern_und_idempotenz_je_empfaenger(versand):
    db, _, _, _, kontakt, incident, _, mails, sms, _ = versand
    kontakt.benachrichtigung_mail = False
    kontakt.telefone_json = json.dumps([
        {"nummer": "+43 660 111", "label": "A", "sms": True},
        {"nummer": "0043-660/222", "label": "B", "sms": True},
    ])
    db.commit()
    assert _run(incident.id)["gesendet"] == 2
    assert _run(incident.id)["gesendet"] == 0
    assert [nummer for nummer, _ in sms] == ["+43660111", "+43660222"]
    assert db.query(ObjektKontaktBenachrichtigung).count() == 2


def test_14_tenant_isolation(versand):
    db, _, _, objekt, kontakt, incident, link, mails, sms, _ = versand
    fremd = FireDept(slug="fremd", name="Fremd", color="#000000")
    db.add(fremd)
    db.flush()
    objekt.org_id = kontakt.org_id = link.org_id = fremd.id
    db.commit()
    _run(incident.id)
    assert not mails and not sms


def test_15_arbeitskopie_erhaelt_alle_felder(versand):
    db, _, _, objekt, kontakt, *_ = versand
    from app.services.objekt_service import erstelle_arbeitskopie, uebernimm_arbeitskopie
    objekt.kontakt_info_uebung = True
    objekt.kontakt_info_stichworte = "B2"
    objekt.kontakt_info_betreff = "Betreff"
    objekt.kontakt_info_template = "Text"
    kontakt.telefone_json = json.dumps([{"nummer": "+43123", "label": None, "sms": True}])
    db.commit()
    kopie = erstelle_arbeitskopie(db, objekt, None)
    db.commit()
    assert kopie.kontakte[0].benachrichtigung_mail
    assert kopie.kontakte[0].sms_nummern == ["+43123"]
    objekt = uebernimm_arbeitskopie(db, kopie, None)
    db.commit()
    assert objekt.kontakt_info_uebung
    assert objekt.kontakt_info_stichworte == "B2" and objekt.kontakt_info_template == "Text"


def test_16_bma_felder_enthalten_keine_telefone(versand):
    _, _, _, _, kontakt, *_ = versand
    from app.services.bma_import.bma_sync import _kontakt_felder
    for feld, wert in _kontakt_felder({"name": "Neu", "telefone": ["1"], "email": "n@e.at"}).items():
        setattr(kontakt, feld, wert)
    assert kontakt.benachrichtigung_mail and kontakt.sms_nummern


def test_17_benachrichtigungsrouten_und_berechtigungen(client):
    from app.core.security import hash_password
    from app.db import SessionLocal
    from app.models.master import SystemSettings
    from app.models.objekt import ObjektChange
    from app.models.user import Role, User, UserRole

    db = SessionLocal()
    set_tenant_context(db, None)
    org = db.query(FireDept).first()
    system = db.get(SystemSettings, "objekt_module_enabled")
    if system is None:
        db.add(SystemSettings(key="objekt_module_enabled", value="true"))
    else:
        system.value = "true"
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    if org_settings is None:
        org_settings = OrgSettings(org_id=org.id)
        db.add(org_settings)
    org_settings.objekt_module_enabled = True
    objekt = Objekt(org_id=org.id, nummer=98761, name="Routenobjekt", status="freigegeben")
    db.add(objekt)
    for username, rollen in (("notify_leser", ("readonly",)), ("notify_verwalter", ("objekt_verwalter",))):
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org.id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            rolle = db.query(Role).filter(Role.code == code).first()
            db.add(UserRole(user_id=user.id, role_id=rolle.id))
    db.commit()
    objekt_id = objekt.id
    db.close()

    def login(name):
        client.cookies.clear()
        client.get("/login")
        csrf = client.cookies.get("ec_csrf")
        client.post("/login", data={"username": name, "password": "Test1234!", "_csrf": csrf})

    login("notify_leser")
    assert client.get(f"/objekte/{objekt_id}/benachrichtigung").status_code == 200
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        f"/objekte/{objekt_id}/benachrichtigung",
        data={"_csrf": csrf},
    ).status_code == 403

    login("notify_verwalter")
    assert client.get(f"/objekte/{objekt_id}/benachrichtigung").status_code == 200
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        f"/objekte/{objekt_id}/benachrichtigung",
        data={"_csrf": csrf, "kontakt_info_betreff": "Test"},
    ).status_code == 200
    assert client.post(
        f"/objekte/{objekt_id}/kontakte/neu",
        data={"_csrf": csrf, "name": "Drei Nummern",
              "telefon_nummer": ["+431", "+432", "+433"],
              "telefon_label": ["A", "B", "C"], "telefon_sms": "1"},
    ).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    assert db.query(ObjektChange).filter(
        ObjektChange.objekt_id == objekt_id,
        ObjektChange.bereich == "benachrichtigung",
    ).count() >= 1
    kontakt = db.query(ObjektKontakt).filter(ObjektKontakt.name == "Drei Nummern").one()
    assert kontakt.sms_nummern == ["+432"]
    db.close()


def test_18_panel_scoping_ueber_einsatz_org_nicht_user_org(versand):
    """Das Objekt-Panel muss auch fuer einen system_admin ohne eigene Org gefuellt sein.

    User.org_id ist bei system_admin NULL (app/models/user.py:38) und beim Org-Wechsel
    per ?org= die Heimat-Org des Admins. Ein Scoping ueber user.org_id wuerde das Panel
    daher stillschweigend leeren - massgeblich ist die Org DES EINSATZES.
    """
    from types import SimpleNamespace

    from app.routers.ui_objekt import _panel_context

    db, org, _, objekt, _, incident, _, _, _, _ = versand
    admin = SimpleNamespace(
        id=1, org_id=None, roles=[SimpleNamespace(code="system_admin")],
        role_codes={"system_admin"},
    )

    ctx = _panel_context(SimpleNamespace(), db, admin, incident.id)

    assert [v.objekt_id for v in ctx["verknuepfungen"]] == [objekt.id]
    assert ctx["hat_empfaenger"][objekt.id] is True
