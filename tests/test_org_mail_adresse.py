"""Adressschema fuer Organisations-E-Mails."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models.master import FireDept, SystemSettings
from app.services.org_mail_adresse import (
    absender_adresse,
    alias_lokalteile,
    empfangs_adresse,
    inbound_domain,
    org_aus_adresse,
    typ_lokalteil,
)


def _org(slug="wolfurt", bos="Feuerwehr", **kwargs):
    return FireDept(slug=slug, name=slug, bos=bos, **kwargs)


def test_normalisierung_und_adressen():
    org = _org("Wölfurt!", "Rotes Kreuz")
    assert typ_lokalteil("Rotes Kreuz") == "rotes-kreuz"
    assert typ_lokalteil("Ärztliche Übung & Spaß") == "aerztliche-uebung-spass"
    assert typ_lokalteil("") == "org"
    assert absender_adresse(org, "einsatzcockpit.com") == "rotes-kreuz.woelfurt@einsatzcockpit.com"
    assert empfangs_adresse(org, "get.einsatzcockpit.com") == "rotes-kreuz.woelfurt@get.einsatzcockpit.com"
    assert alias_lokalteile(org) == ["woelfurt"]


def test_org_aus_adresse_beruecksichtigt_domain_case_alias_und_status(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mail-address.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    fw = _org("gleich", "Feuerwehr")
    rk = _org("andere", "Rotes Kreuz")
    inactive = _org("inaktiv", "Polizei", is_active=False)
    db.add_all([fw, rk, inactive])
    db.commit()
    assert org_aus_adresse(db, "FEUERWEHR.GLEICH@get.einsatzcockpit.com", "get.einsatzcockpit.com") == fw
    assert org_aus_adresse(db, "gleich@get.einsatzcockpit.com", "get.einsatzcockpit.com") == fw
    assert org_aus_adresse(db, "feuerwehr.gleich@einsatzcockpit.com", "get.einsatzcockpit.com") is None
    assert org_aus_adresse(db, "rotes-kreuz.gleich@get.einsatzcockpit.com", "get.einsatzcockpit.com") is None
    assert org_aus_adresse(db, "polizei.inaktiv@get.einsatzcockpit.com", "get.einsatzcockpit.com") is None


def test_inbound_domain_systemwert_fallback_und_validierung(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'inbound-domain.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(settings, "MAIL_INBOUND_DOMAIN", "env.example.at")
    assert inbound_domain(db) == "env.example.at"
    db.add(SystemSettings(key="resend_inbound_domain", value="get.example.at"))
    db.commit()
    assert inbound_domain(db) == "get.example.at"
    db.get(SystemSettings, "resend_inbound_domain").value = "bad\n.example"
    db.commit()
    assert inbound_domain(db) == "env.example.at"
