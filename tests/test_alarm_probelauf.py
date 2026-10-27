"""Probelauf: Vergleich, Org-Grenze, Mengenlimit und unveraenderte DB."""

import subprocess
from copy import deepcopy

import pytest
from sqlalchemy import inspect

from app.models.gateway import AlarmIngest, AlarmParserTestfall, Gateway, PrintJob
from app.models.incident import Incident, IncidentAlarmJob
from app.models.master import FireDept
from app.services.alarm_parser import STANDARD_REGELSATZ, Regelsatz
from app.services.alarm_parser.probelauf import feld_diff, probelauf
from tests import test_alarm_apply_service as basis

db = basis.db


def regeln(**aenderungen):
    return Regelsatz.model_validate({**STANDARD_REGELSATZ.model_dump(), **aenderungen})


def test_klassifikation_und_nur_angegebene_erwartungen(db):
    gw = db.info["gateway"]
    row = basis.store(db, basis.nummer() + " t1 > wasser")
    test = AlarmParserTestfall(org_id=gw.org_id, titel="Stichwort", rohtext=row.raw_text,
                               erwartet={"stichwort": "t1"}, aktiv=True)
    db.add(test)
    db.commit()
    gleich = probelauf(db, gw.org_id, STANDARD_REGELSATZ)
    assert gleich.zusammenfassung == {"regression": 0, "neu_korrekt": 0, "unveraendert": 2, "testfall_fehler": 0}
    assert gleich.freigabefaehig
    schlechter = regeln(stichwort_muster=r"z\d{1,2}")
    result = probelauf(db, gw.org_id, schlechter)
    assert result.zusammenfassung["regression"] == 2
    assert result.zusammenfassung["testfall_fehler"] == 1
    assert result.datensaetze[0]["klassifikation"] == "regression"
    test_result = next(r for r in result.datensaetze if r["art"] == "testfall")
    assert test_result["bestand"] is True and test_result["besteht"] is False
    assert test_result["erwartungsfehler"] == [{"feld": "stichwort", "erwartet": "t1", "tatsaechlich": None}]
    assert any(d["feld"] == "stichwort" for d in test_result["diff"])
    gewichte = deepcopy(STANDARD_REGELSATZ.qualitaetsgewichte)
    gewichte["stichwort"] += 1
    besser = probelauf(db, gw.org_id, regeln(qualitaetsgewichte=gewichte))
    assert besser.zusammenfassung["neu_korrekt"] == 2


def test_neu_korrekter_testfall_bei_gleichem_status(db):
    gw = db.info["gateway"]
    db.add(AlarmParserTestfall(org_id=gw.org_id, titel="Neues Stichwort", rohtext="f26008809 z2 > wasser",
                             erwartet={"stichwort": "z2"}, aktiv=True))
    db.commit()
    result = probelauf(db, gw.org_id, regeln(stichwort_muster=r"[ftz]\d{1,2}"))
    assert result.zusammenfassung["neu_korrekt"] == 1
    assert result.datensaetze[0]["bestand"] is False
    assert result.datensaetze[0]["besteht"] is True


def test_rein_lesend_und_org_filter(db):
    gw = db.info["gateway"]
    row = basis.apply(db, basis.nummer() + " t1 > wasser")
    andere_org = FireDept(slug=basis.uuid4().hex, name="Andere")
    db.add(andere_org)
    db.flush()
    fremd_gw = Gateway(org_id=andere_org.id, name="Andere")
    db.add(fremd_gw)
    db.flush()
    fremd = basis.store(db, basis.nummer() + " t1 > fremd", gw=fremd_gw)
    db.add_all([
        AlarmParserTestfall(org_id=andere_org.id, titel="Fremd", rohtext="fremd", erwartet={"status": "ok"}),
        AlarmParserTestfall(org_id=gw.org_id, titel="Inaktiv", rohtext="leer", erwartet={"status": "ok"}, aktiv=False),
    ])
    db.commit()
    modelle = (Incident, AlarmIngest, IncidentAlarmJob, PrintJob)
    vorher = [db.query(model).count() for model in modelle]
    inhalt = {attr.key: deepcopy(getattr(row, attr.key)) for attr in inspect(AlarmIngest).column_attrs}
    result = probelauf(db, gw.org_id, STANDARD_REGELSATZ, ingest_ids=[row.id, fremd.id])
    assert [(r["art"], r["id"]) for r in result.datensaetze] == [("ingest", row.id)]
    assert vorher == [db.query(model).count() for model in modelle]
    db.refresh(row)
    assert inhalt == {attr.key: getattr(row, attr.key) for attr in inspect(AlarmIngest).column_attrs}
    assert not db.new and not db.dirty and not db.deleted


def test_limit_und_zeitlimit(db, monkeypatch):
    gw = db.info["gateway"]
    for i in range(202):
        db.add(AlarmIngest(org_id=gw.org_id, gateway_id=gw.id, raw_hash=str(i), raw_text="leer"))
    db.commit()
    assert len(probelauf(db, gw.org_id, STANDARD_REGELSATZ, letzte_n=999).datensaetze) == 200
    assert len(probelauf(db, gw.org_id, STANDARD_REGELSATZ, letzte_n=2).datensaetze) == 2
    with pytest.raises(ValueError, match="200"):
        probelauf(db, gw.org_id, STANDARD_REGELSATZ, ingest_ids=list(range(201)))

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr("app.services.alarm_parser.probelauf.subprocess.run", timeout)
    with pytest.raises(ValueError, match="abgebrochen"):
        probelauf(db, gw.org_id, STANDARD_REGELSATZ)


def test_regel_diff_strukturiert():
    result = feld_diff({"version": "1", "aliase": {"alt": "A", "gleich": "B"}, "x": [1]},
                       {"version": "2", "aliase": {"neu": "C", "gleich": "D"}, "x": [1]})
    assert result == [
        {"feld": "aliase.alt", "art": "entfernt", "vorher": "A", "nachher": None},
        {"feld": "aliase.gleich", "art": "geaendert", "vorher": "B", "nachher": "D"},
        {"feld": "aliase.neu", "art": "hinzugefuegt", "vorher": None, "nachher": "C"},
    ]
