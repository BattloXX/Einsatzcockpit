"""ECPG PR1: Feature-Flag, Pairing, Artifact-Signatur, Idempotenz, Job-Anlage,
serieller Ingest, printer_report, Tenant-Isolation."""
# ruff: noqa: E402  # App-Imports erst nach Registrierung des SQLite-BigInteger-Compilers.
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.security import (
    hash_api_key,
    sign_artifact_token,
    unsign_artifact_token,
)
from app.core.tenant import set_tenant_context
from app.models.gateway import (
    GATEWAY_STATUS_OFFLINE,
    GATEWAY_STATUS_UNPAIRED,
    Gateway,
    Printer,
)
from app.services import gateway_service as gw_svc
from app.services import print_dispatcher as disp


@pytest.fixture
def db(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


_ORG_A = 991001
_ORG_B = 991002


# ── Feature-Flag ────────────────────────────────────────────────────────────────

class _Sys:
    def __init__(self, value=None):
        self.key = "gateway_module_enabled"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.gateway_module_enabled = enabled


def _flag_db(sys_value, org_enabled):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys(sys_value)
    db.query.return_value.filter.return_value.execution_options.return_value.first.return_value = _OrgS(org_enabled)
    return db


def test_system_flag_missing_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert gw_svc.gateway_system_enabled(db) is False


def test_effective_false_when_no_org():
    assert gw_svc.gateway_effective_enabled(None, MagicMock()) is False


def test_effective_false_when_system_off():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("false", True)) is False


def test_effective_false_when_org_off():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("true", False)) is False


def test_effective_true_when_both_on():
    assert gw_svc.gateway_effective_enabled(1, _flag_db("true", True)) is True


# ── Artifact-Signatur ────────────────────────────────────────────────────────────

def test_artifact_token_roundtrip():
    tok = sign_artifact_token(55, 7)
    assert unsign_artifact_token(tok) == (55, 7)


def test_artifact_token_tampered_rejected():
    tok = sign_artifact_token(55, 7)
    assert unsign_artifact_token(tok + "x") is None


def test_artifact_token_garbage_rejected():
    assert unsign_artifact_token("not-a-token") is None


def test_verify_artifact_wrong_job_rejected():
    from app.services.print_artifact_service import verify_artifact_token
    tok = sign_artifact_token(55, 7)
    assert verify_artifact_token(99, tok) is None
    assert verify_artifact_token(55, tok) == 7


# ── Pairing ──────────────────────────────────────────────────────────────────────

def _make_gateway(db, org_id=_ORG_A, name="GW"):
    gw = Gateway(org_id=org_id, name=name)
    db.add(gw)
    db.flush()
    return gw


def test_pairing_success_sets_token_and_clears_code(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    db.flush()
    assert gw.pairing_code_hash and gw.pairing_expires_at

    result = gw_svc.pair_gateway(db, code)
    assert result is not None
    paired, raw_token = result
    assert paired.id == gw.id
    assert paired.device_token_hash == hash_api_key(raw_token)
    assert paired.pairing_code_hash is None
    assert paired.status == GATEWAY_STATUS_OFFLINE


def test_pairing_wrong_code_fails(db):
    gw = _make_gateway(db)
    gw_svc.erzeuge_pairing_code(db, gw)
    db.flush()
    assert gw_svc.pair_gateway(db, "WRONGCOD") is None


def test_pairing_expired_code_fails(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    gw.pairing_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.flush()
    assert gw_svc.pair_gateway(db, code) is None


def test_rotate_and_revoke(db):
    gw = _make_gateway(db)
    code = gw_svc.erzeuge_pairing_code(db, gw)
    _, first_token = gw_svc.pair_gateway(db, code)
    db.flush()
    new_token = gw_svc.rotate_token(db, gw)
    assert new_token != first_token
    assert gw.device_token_hash == hash_api_key(new_token)

    gw_svc.revoke_token(gw)
    assert gw.device_token_hash is None
    assert gw.status == GATEWAY_STATUS_UNPAIRED


# ── Idempotenz + Job-Anlage ──────────────────────────────────────────────────────

def test_idempotency_manual_always_unique():
    k1 = disp.build_idempotency_key(org_id=_ORG_A, source="manual", rule_id=None, incident_id=1,
                                    gsl_id=None, objekt_id=None, document_type="einsatzinfo",
                                    artifact_ref=None, printer_id=2)
    k2 = disp.build_idempotency_key(org_id=_ORG_A, source="manual", rule_id=None, incident_id=1,
                                    gsl_id=None, objekt_id=None, document_type="einsatzinfo",
                                    artifact_ref=None, printer_id=2)
    assert k1 != k2 and k1.startswith("manual:")


def test_idempotency_rule_deterministic():
    kw = dict(org_id=_ORG_A, source="rule", rule_id=3, incident_id=1, gsl_id=None, objekt_id=None,
              document_type="einsatzinfo", artifact_ref=None, printer_id=2)
    assert disp.build_idempotency_key(**kw) == disp.build_idempotency_key(**kw)
    assert disp.build_idempotency_key(**kw) != disp.build_idempotency_key(**(kw | {"org_id": _ORG_B}))


def test_create_print_job_rule_dedup(db):
    gw = _make_gateway(db)
    job1, created1 = disp.create_print_job(
        db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1, document_type="einsatzinfo",
        source="rule", rule_id=5, incident_id=100,
    )
    job2, created2 = disp.create_print_job(
        db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1, document_type="einsatzinfo",
        source="rule", rule_id=5, incident_id=100,
    )
    assert created1 is True and created2 is False
    assert job1.id == job2.id


def test_create_print_job_race_liefert_bestehenden_job():
    """Ein Unique-Konflikt zwischen Vorabprüfung und Insert bleibt idempotent."""
    fake_db = MagicMock()
    existing = MagicMock(id=77)
    abfrage = fake_db.query.return_value.filter.return_value.execution_options.return_value
    abfrage.first.side_effect = [None, existing]
    fake_db.flush.side_effect = IntegrityError("insert", {}, Exception("unique"))

    job, created = disp.create_print_job(
        fake_db, org_id=_ORG_A, gateway_id=1, printer_id=2,
        document_type="einsatzinfo", source="rule", rule_id=5, incident_id=100,
    )

    assert job is existing
    assert created is False


def test_create_print_job_manual_new_each_time(db):
    gw = _make_gateway(db)
    j1, c1 = disp.create_print_job(db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1,
                                   document_type="einsatzinfo", source="manual", incident_id=100)
    j2, c2 = disp.create_print_job(db, org_id=_ORG_A, gateway_id=gw.id, printer_id=1,
                                   document_type="einsatzinfo", source="manual", incident_id=100)
    assert c1 and c2 and j1.id != j2.id


# ── Druckregel-Filter (on_event) ─────────────────────────────────────────────────

def test_filter_min_alarmstufe():
    rule = MagicMock()
    rule.trigger = "einsatz_created"
    rule.filters = {"min_alarmstufe": 3}
    assert disp._filter_matches(rule, {"alarmstufe": 5}) is True
    assert disp._filter_matches(rule, {"alarmstufe": 1}) is False
    assert disp._filter_matches(rule, {"alarmstufe": None}) is False


@pytest.mark.parametrize(
    ("filterwert", "is_exercise", "erwartet"),
    [
        (None, False, True), (None, True, True),
        ("alle", False, True), ("alle", True, True),
        ("nur_echt", False, True), ("nur_echt", True, False),
        ("nur_uebung", False, False), ("nur_uebung", True, True),
    ],
)
def test_filter_uebung_matrix(filterwert, is_exercise, erwartet):
    rule = MagicMock(id=7)
    rule.filters = {} if filterwert is None else {"uebung": filterwert}
    assert disp._filter_matches(rule, {"is_exercise": is_exercise}) is erwartet


@pytest.mark.parametrize("filterwert", ["nur_echt", "nur_uebung"])
def test_filter_uebung_ohne_kontext_fail_closed(filterwert, caplog):
    rule = MagicMock(id=7, filters={"uebung": filterwert})
    rule.filters = {"uebung": filterwert}
    assert disp._filter_matches(rule, {}) is False
    assert "Übungsstatus" in caplog.text


def test_verleih_created_filtert_nach_uebungsstatus():
    rule = MagicMock(id=8, trigger="verleih_created")
    rule.filters = {"uebung": "nur_uebung"}
    assert disp._filter_matches(rule, {"is_exercise": True}) is True
    assert disp._filter_matches(rule, {"is_exercise": False}) is False


def test_filter_nur_bma_mit_bma_kontext():
    rule = MagicMock(filters={"nur_bma": True})
    rule.filters = {"nur_bma": True}
    rule.trigger = "einsatz_created"
    assert disp._filter_matches(rule, {"nur_bma": True}) is True


def test_incident_context_erkennt_bestaetigtes_bma_objekt(db):
    from app.models.incident import Incident
    from app.models.objekt import Objekt, ObjektBMA, ObjektEinsatz

    incident = Incident(primary_org_id=_ORG_A, alarm_type_code="B3", is_exercise=False)
    objekt = Objekt(org_id=_ORG_A, name="BMA-Testobjekt")
    db.add_all([incident, objekt])
    db.flush()
    db.add(ObjektBMA(org_id=_ORG_A, objekt_id=objekt.id, bma_nummer="4711"))
    db.add(ObjektEinsatz(
        org_id=_ORG_A, incident_id=incident.id, objekt_id=objekt.id,
        quelle="bma", status="bestaetigt",
    ))
    db.flush()

    context = disp._incident_context(incident)
    assert context["nur_bma"] is True
    assert context["alarmstufe"] == 3


def test_incident_context_alarmstufe_ohne_zahl_unbekannt():
    incident = MagicMock(id=8, alarm_type_code="BMA", reason="Brandmeldeanlage",
                         report_text=None, is_exercise=False,
                         objekt_verknuepfungen=[])
    context = disp._incident_context(incident)
    assert context["alarmstufe"] is None
    rule = MagicMock(filters={"min_alarmstufe": 2})
    rule.filters = {"min_alarmstufe": 2}
    rule.trigger = "einsatz_created"
    assert disp._filter_matches(rule, context) is False


def test_filter_stichwort():
    rule = MagicMock()
    rule.filters = {"stichwort": ["Brand"]}
    assert disp._filter_matches(rule, {"stichwort": "B3 Brand groß"}) is True
    assert disp._filter_matches(rule, {"stichwort": "T1 technisch"}) is False


def test_filter_zeitfenster_tag():
    """Fenster innerhalb eines Tages (08:00–18:00)."""
    rule = MagicMock()
    rule.filters = {"zeitfenster": {"von": "08:00", "bis": "18:00"}}
    assert disp._filter_matches(rule, {"now_hhmm": "12:00"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "06:00"}) is False
    # Ohne bekannte Uhrzeit greift das Fenster nicht (kein Ausschluss)
    assert disp._filter_matches(rule, {"now_hhmm": None}) is True


def test_filter_zeitfenster_ueber_mitternacht():
    """Fenster über Mitternacht (22:00–06:00)."""
    rule = MagicMock()
    rule.filters = {"zeitfenster": {"von": "22:00", "bis": "06:00"}}
    assert disp._filter_matches(rule, {"now_hhmm": "23:30"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "05:00"}) is True
    assert disp._filter_matches(rule, {"now_hhmm": "12:00"}) is False


def test_on_event_creates_jobs_and_dedups(db):
    """einsatz_created mit aktiver Regel → Job je Dokument×Drucker, idempotent."""
    from app.models.gateway import PrintRule
    from app.models.master import OrgSettings, SystemSettings

    org = 991500
    # Modul systemweit + org-seitig aktiv
    if not db.query(SystemSettings).filter(SystemSettings.key == "gateway_module_enabled").first():
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    db.add(OrgSettings(org_id=org, gateway_module_enabled=True))
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("tok-" + str(org)))
    db.add(gw)
    db.flush()
    rule = PrintRule(org_id=org, name="Einsatzinfo bei Alarm", aktiv=True,
                     trigger="einsatz_created", documents=["einsatzinfo"], printer_ids=[gw.id])
    db.add(rule)
    db.flush()

    jobs1 = disp.on_event(db, org, "einsatz_created", {"incident_id": 555})
    assert len(jobs1) == 1
    jobs2 = disp.on_event(db, org, "einsatz_created", {"incident_id": 555})
    assert len(jobs2) == 0  # dedupliziert (gleicher Einsatz/Regel/Dokument/Drucker)


def test_on_event_empty_when_module_off(db):
    # Org ohne Flag → keine Jobs
    jobs = disp.on_event(db, 991600, "einsatz_created", {"incident_id": 1})
    assert jobs == []


def test_verleih_created_ignoriert_min_alarmstufe_und_nur_bma(db):
    """Einsatzfilter blockieren einen nicht einsatzbezogenen Verleih-Trigger nicht."""
    from app.models.gateway import PrintRule
    from app.models.master import OrgSettings, SystemSettings

    org = 991602
    system_flag = (
        db.query(SystemSettings)
        .filter(SystemSettings.key == "gateway_module_enabled")
        .first()
    )
    if system_flag is None:
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    else:
        system_flag.value = "true"
    db.add(OrgSettings(org_id=org, gateway_module_enabled=True))
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("verleih-tok"))
    db.add(gw)
    db.flush()
    rule = PrintRule(
        org_id=org, name="Verleih trotz Einsatzfilter", aktiv=True,
        trigger="verleih_created", documents=["verleih_schein"], printer_ids=[1],
        filters={"min_alarmstufe": 4, "nur_bma": True},
    )
    db.add(rule)
    db.flush()

    jobs = disp.on_event(
        db, org, "verleih_created", {"gsl_id": 3, "ausleihe_id": 88},
    )

    assert len(jobs) == 1
    assert jobs[0].document_type == "verleih_schein"
    assert jobs[0].artifact_ref == "88"


@pytest.mark.asyncio
async def test_autoprint_verleih_background_reicht_uebungsstatus_durch(db, monkeypatch):
    from app.models.gateway import PrintJob, PrintRule
    from app.models.major_incident import MajorIncident
    from app.models.master import OrgSettings, SystemSettings
    from app.models.verleih import VerleihAusleihe

    org = 991603
    system_flag = db.query(SystemSettings).filter(
        SystemSettings.key == "gateway_module_enabled"
    ).first()
    if system_flag is None:
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    else:
        system_flag.value = "true"
    db.add(OrgSettings(org_id=org, gateway_module_enabled=True))
    gw = Gateway(org_id=org, name="Verleih-GW", device_token_hash=hash_api_key("bg-tok"))
    db.add(gw)
    db.flush()
    db.add(PrintRule(
        org_id=org, name="Nur Uebungsverleih", aktiv=True, trigger="verleih_created",
        documents=["verleih_schein"], printer_ids=[gw.id],
        filters={"uebung": "nur_uebung"},
    ))
    uebung = MajorIncident(org_id=org, name="Uebung", is_exercise=True)
    echt = MajorIncident(org_id=org, name="Echt", is_exercise=False)
    db.add_all([uebung, echt])
    db.flush()
    ausleihe_uebung = VerleihAusleihe(org_id=org, lage_id=uebung.id, name="Uebung")
    ausleihe_echt = VerleihAusleihe(org_id=org, lage_id=echt.id, name="Echt")
    db.add_all([ausleihe_uebung, ausleihe_echt])
    db.commit()

    zugestellt = []

    async def _dispatch(_db, job):
        zugestellt.append(job.artifact_ref)

    monkeypatch.setattr(disp, "dispatch_job", _dispatch)
    await disp.autoprint_verleih_background(ausleihe_uebung.id)
    await disp.autoprint_verleih_background(ausleihe_echt.id)

    db.expire_all()
    jobs = db.query(PrintJob).filter(PrintJob.org_id == org).all()
    assert [job.artifact_ref for job in jobs] == [str(ausleihe_uebung.id)]
    assert zugestellt == [str(ausleihe_uebung.id)]


def test_gsl_created_ueberspringt_einsatzinfo(db, caplog):
    """Ein unpassender Altbestand erzeugt keinen Job und erreicht keinen Renderer."""
    from app.models.gateway import PrintRule

    org = 991601
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("gsl-tok"))
    db.add(gw)
    db.flush()
    rule = PrintRule(
        org_id=org, name="Ungültiger Altbestand", aktiv=True, trigger="gsl_created",
        documents=["einsatzinfo"], printer_ids=[1],
    )
    db.add(rule)
    db.flush()

    assert disp._jobs_for_rule(db, gw, rule, {"gsl_id": 44}) == []
    assert "übersprungen" in caplog.text


def test_objekt_elements_create_dokumentseiten_jobs(db):
    """Regel mit objekt_elements erzeugt DOC_OBJEKT_DOKUMENT-Jobs je passender Seite
    des am Einsatz bestätigt verknüpften Objekts."""
    from app.models.gateway import DOC_OBJEKT_DOKUMENT, PrintRule
    from app.models.master import OrgSettings, SystemSettings
    from app.models.objekt import (
        OBJEKT_EINSATZ_BESTAETIGT,
        Objekt,
        ObjektDokument,
        ObjektDokumentSeite,
        ObjektEinsatz,
    )

    org = 991700
    if not db.query(SystemSettings).filter(SystemSettings.key == "gateway_module_enabled").first():
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    db.add(OrgSettings(org_id=org, gateway_module_enabled=True))
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("tok-" + str(org)))
    db.add(gw)
    db.flush()

    objekt = Objekt(org_id=org, nummer="OBJ-1", name="Schule")
    db.add(objekt)
    db.flush()
    dok = ObjektDokument(org_id=org, objekt_id=objekt.id, dateiname_original="plan.pdf", pfad="p/plan.pdf")
    db.add(dok)
    db.flush()
    # Eine „bei Einsatz drucken"-Seite (mit Einzel-PDF) + eine ohne Flag → nur erste zählt
    db.add(ObjektDokumentSeite(org_id=org, objekt_id=objekt.id, dokument_id=dok.id, seiten_nr=1,
                               einzel_pdf_pfad="p/plan_1.pdf", bei_einsatz_drucken=True))
    db.add(ObjektDokumentSeite(org_id=org, objekt_id=objekt.id, dokument_id=dok.id, seiten_nr=2,
                               einzel_pdf_pfad="p/plan_2.pdf", bei_einsatz_drucken=False))
    # Objekt bestätigt mit dem Einsatz verknüpft
    db.add(ObjektEinsatz(org_id=org, objekt_id=objekt.id, incident_id=777,
                         quelle="manuell", status=OBJEKT_EINSATZ_BESTAETIGT))
    rule = PrintRule(org_id=org, name="Objektunterlagen", aktiv=True, trigger="einsatz_created",
                     objekt_elements=["bei_einsatz_drucken"], printer_ids=[gw.id])
    db.add(rule)
    db.flush()

    jobs = disp.on_event(db, org, "einsatz_created", {"incident_id": 777})
    assert len(jobs) == 1
    assert jobs[0].document_type == DOC_OBJEKT_DOKUMENT
    assert jobs[0].objekt_id == objekt.id
    # idempotent
    assert disp.on_event(db, org, "einsatz_created", {"incident_id": 777}) == []


def test_build_test_jobs_ignores_trigger_and_filters(db):
    """Testdruck erzeugt Jobs unabhängig von Trigger/aktiv/Filter, mit source=manual."""
    from app.models.gateway import JOB_SOURCE_MANUAL, PrintRule

    org = 991800
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("tok-" + str(org)))
    db.add(gw)
    db.flush()
    rule = PrintRule(org_id=org, name="Nur manuell", aktiv=False, trigger="manual_only",
                     documents=["einsatzinfo"], printer_ids=[gw.id],
                     filters={"min_alarmstufe": 9})  # Filter würde sonst blocken
    db.add(rule)
    db.flush()
    jobs = disp.build_test_jobs(db, rule, {"incident_id": 42})
    assert len(jobs) == 1
    assert jobs[0].source == JOB_SOURCE_MANUAL
    assert jobs[0].incident_id == 42


def test_idempotency_key_fallback_segment_is_conditional():
    kwargs = dict(
        org_id=7, source="rule", rule_id=11, incident_id=13, gsl_id=None,
        objekt_id=None, document_type="einsatzinfo", artifact_ref=None, printer_id=17,
    )
    # Altbestand-Kompatibilitaet: Dieser Literalwert darf sich ohne fallback_of nie aendern.
    assert disp.build_idempotency_key(**kwargs) == "rule:9b8c4878eb6da9a09c4954229e2643d1"
    assert disp.build_idempotency_key(**kwargs, fallback_of=19) == disp.build_idempotency_key(
        **kwargs, fallback_of=19,
    )
    assert disp.build_idempotency_key(**kwargs, fallback_of=19) != disp.build_idempotency_key(**kwargs)


def test_resolve_test_context_alle_ausloeser_und_gsl_org_isolation(db):
    from app.models.gateway import AlarmIngest, PrintRule
    from app.models.incident import Incident
    from app.models.major_incident import MajorIncident
    from app.models.verleih import VerleihAusleihe

    org = 1
    fremde_lage = MajorIncident(org_id=2, name="Fremd")
    lage = MajorIncident(org_id=org, name="Eigene Lage", is_exercise=True)
    inc = Incident(primary_org_id=org, reason="B3")
    db.add_all([fremde_lage, lage, inc])
    db.flush()
    ausleihe = VerleihAusleihe(org_id=org, lage_id=lage.id, name="Test")
    gw = Gateway(org_id=org, name="Kontext-GW")
    db.add_all([ausleihe, gw])
    db.flush()
    ingest = AlarmIngest(
        org_id=org, gateway_id=gw.id, raw_hash=uuid.uuid4().hex, raw_text="ALARM",
        parse_status="parsed", einsatz_id=inc.id,
    )
    db.add(ingest)
    db.flush()

    erwartet = {
        "einsatz_created": ("einsatz", inc.id, "incident_id", inc.id),
        "gsl_created": ("gsl", lage.id, "gsl_id", lage.id),
        "verleih_created": ("verleih", ausleihe.id, "gsl_id", lage.id),
        "alarm_serial_received": ("alarm", ingest.id, "alarm_ingest_id", ingest.id),
    }
    for trigger, (art, ref_id, key, value) in erwartet.items():
        rule = PrintRule(org_id=org, name=trigger, trigger=trigger)
        bezug = disp.resolve_test_context(db, rule)
        assert bezug is not None
        assert (bezug.art, bezug.ref_id, bezug.context[key]) == (art, ref_id, value)


def test_test_jobs_gsl_verleih_und_alarm_setzen_renderbezug(db):
    from app.models.gateway import PrintRule

    org = 991850
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("ctx"))
    db.add(gw)
    db.flush()
    faelle = [
        ("gsl_created", ["gsl_lageblatt"], {"gsl_id": 71}, "gsl_id", 71),
        ("verleih_created", ["verleih_schein"], {"gsl_id": 72, "ausleihe_id": 73}, "artifact_ref", "73"),
        ("alarm_serial_received", ["alarm_rohtext"], {"alarm_ingest_id": 74}, "artifact_ref", "74"),
    ]
    for trigger, documents, context, attribut, wert in faelle:
        rule = PrintRule(
            org_id=org, name=trigger, trigger=trigger, documents=documents,
            printer_ids=[101],
        )
        db.add(rule)
        db.flush()
        jobs = disp.build_test_jobs(db, rule, context)
        assert len(jobs) == 1
        assert getattr(jobs[0], attribut) == wert


def test_alarm_rohtext_ohne_alarmbezug_wird_uebersprungen(db, caplog):
    """Alarm-Rohtext ohne AlarmIngest im Kontext darf weder crashen noch leer drucken.

    Erreichbar ueber den Testdruck einer Altbestand-Regel, deren Dokumente nicht zum
    Ausloeser passen: der Testdruck umgeht die TRIGGER_DOCUMENT_TYPES-Allowlist
    (_jobs_for_rule, source=manual), der Einsatz-Kontext hat aber kein alarm_ingest_id.
    """
    from app.models.gateway import PrintRule

    org = 991851
    gw = Gateway(org_id=org, name="GW", device_token_hash=hash_api_key("alarm-ohne-bezug"))
    db.add(gw)
    db.flush()
    rule = PrintRule(
        org_id=org, name="Altbestand Rohtext", trigger="einsatz_created",
        documents=["alarm_rohtext"], printer_ids=[101],
    )
    db.add(rule)
    db.flush()

    with caplog.at_level("WARNING"):
        jobs = disp.build_test_jobs(db, rule, {"incident_id": 4711})

    assert jobs == []
    assert "ohne Alarm-Bezug" in caplog.text


# ── Serieller Ingest (Idempotenz via raw_hash) ───────────────────────────────────

def test_serial_ingest_idempotent(db, monkeypatch):
    import app.services.serial_alarm_service as sas
    # Einsatz-Anlage isolieren – wir testen nur die raw_hash-Idempotenz.
    monkeypatch.setattr(sas, "_create_or_link_incident", lambda *a, **k: (None, None))

    raw = "ALARM 1234\nB3 Brand\nWolfurt Kirchstrasse 1"
    ing1, created1 = sas.ingest_alarm(db, org_id=_ORG_A, gateway_id=1, raw_text=raw,
                                       charset="cp850", parsed=None, parse_status="parse_failed")
    ing2, created2 = sas.ingest_alarm(db, org_id=_ORG_A, gateway_id=1, raw_text=raw,
                                       charset="cp850", parsed=None, parse_status="parse_failed")
    assert created1 is True and created2 is False
    assert ing1.id == ing2.id


# ── printer_report ───────────────────────────────────────────────────────────────

def test_printer_report_creates_suggestion_and_updates(db):
    from app.services.printer_report_service import apply_printer_report
    gw = _make_gateway(db)
    db.commit()

    apply_printer_report(gw.id, _ORG_A, {"printers": [
        {"name": "Bürodrucker", "uri": "ipp://10.0.0.5/ipp/print",
         "identity": {"serial": "ABC123"}, "capabilities": {"duplex": True}},
    ]})
    p = db.query(Printer).filter(Printer.gateway_id == gw.id).first()
    assert p is not None and p.aktiv is False and p.uri.endswith("/ipp/print")

    # gleiche Identität, neue IP → Update statt Duplikat
    apply_printer_report(gw.id, _ORG_A, {"printers": [
        {"name": "Bürodrucker", "uri": "ipp://10.0.0.9/ipp/print",
         "identity": {"serial": "ABC123"}},
    ]})
    db.expire_all()
    printers = db.query(Printer).filter(Printer.gateway_id == gw.id).all()
    assert len(printers) == 1
    assert printers[0].uri == "ipp://10.0.0.9/ipp/print"


def test_printer_status_updates_reachable_by_id(db):
    """Periodischer Health-Check aktualisiert reachable/checked_at id-basiert,
    nur für Drucker DIESES Gateways."""
    from app.services.printer_report_service import apply_printer_status
    gw = _make_gateway(db)
    p1 = Printer(org_id=_ORG_A, gateway_id=gw.id, name="D1", uri="ipp://10.0.0.5/x", aktiv=True)
    p2 = Printer(org_id=_ORG_A, gateway_id=gw.id, name="D2", uri="ipp://10.0.0.6/x", aktiv=True)
    db.add_all([p1, p2])
    db.commit()

    apply_printer_status(gw.id, _ORG_A, {"printers": [
        {"printer_id": p1.id, "status": {"reachable": True, "state": "idle"}},
        {"printer_id": p2.id, "status": {"reachable": False}},
    ]})
    db.expire_all()
    assert db.get(Printer, p1.id).status.get("reachable") is True
    assert db.get(Printer, p1.id).status.get("checked_at")  # default gesetzt
    assert db.get(Printer, p2.id).status.get("reachable") is False


# ── Tenant-Isolation ─────────────────────────────────────────────────────────────

def test_tenant_isolation_gateway(setup_db):
    eng = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    s = Session()
    set_tenant_context(s, None)
    gw_a = Gateway(org_id=_ORG_A, name="A-Gateway")
    gw_b = Gateway(org_id=_ORG_B, name="B-Gateway")
    s.add_all([gw_a, gw_b])
    s.commit()

    # Kontext Org B → sieht nur eigene Gateways
    set_tenant_context(s, _ORG_B)
    visible = s.query(Gateway).filter(Gateway.name.in_(["A-Gateway", "B-Gateway"])).all()
    names = {g.name for g in visible}
    s.close()
    assert "B-Gateway" in names
    assert "A-Gateway" not in names
