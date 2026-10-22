"""ECP-07: Objektinformationen (Gefahren, Hinweise, BMA, Zufahrt) in KI-Vorschlaegen."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, Task
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_EINSATZ_BESTAETIGT,
    OBJEKT_EINSATZ_VORSCHLAG,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    Objekt,
    ObjektBMA,
    ObjektEinsatz,
    ObjektGefahr,
)
from app.services.objekt_ki_kontext import (
    gefahren_auftraege,
    gefahren_hinweise,
    lade_objekt_kontext,
    objekt_ki_kontext,
)


def _setup(*, status=OBJEKT_STATUS_FREIGEGEBEN, verknuepfung=OBJEKT_EINSATZ_BESTAETIGT, mit_gefahr=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=f"kikontext-{uuid.uuid4().hex[:8]}", name="KI-Kontext", color="#111111", bos="Feuerwehr")
    db.add(org)
    db.flush()
    kat = GefahrenKatalog(org_id=org.id, name="Gasanschluss", piktogramm_typ="gas", aktiv=True)
    db.add(kat)
    db.flush()
    objekt = Objekt(
        org_id=org.id, nummer=4711, name="Lagerhalle Muster", strasse="Hauptstrasse", hausnummer="5",
        plz="6922", ort="Wolfurt", status=status,
        informationen="Dach mit Photovoltaik, Notabschaltung beim Haupteingang.",
        anfahrtsweg="Zufahrt ueber Nordtor, Tor wird mit Schluessel F2 geoeffnet.",
    )
    db.add(objekt)
    db.flush()
    if mit_gefahr:
        db.add(ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=kat.id, un_nummer="1965",
                            gefahrnummer="23", stoffname="Fluessiggas", detail="Tank 5 t im Hof"))
    db.add(ObjektBMA(org_id=org.id, objekt_id=objekt.id, bma_nummer="B-77", bmz_standort="Eingang Nord",
                     schluesselsafe_vorhanden=True, schluesselsafe_standort="neben BMZ",
                     benachrichtigung_sms="+436641234567"))
    inc = Incident(primary_org_id=org.id, alarm_type_code="B2", reason="Brand", status="active",
                   started_at=datetime.now(UTC))
    db.add(inc)
    db.flush()
    db.add(ObjektEinsatz(org_id=org.id, objekt_id=objekt.id, incident_id=inc.id, quelle="manuell",
                         status=verknuepfung))
    db.commit()
    return db, inc.id


def test_kontext_enthaelt_gefahren_hinweise_zufahrt_und_bma_ohne_kontaktdaten():
    db, incident_id = _setup()
    try:
        kontext = objekt_ki_kontext(db, incident_id)
        assert "Lagerhalle Muster" in kontext.text and "Nr. 4711" in kontext.text
        assert "SICHERHEITSKRITISCH - Gefahren: Fluessiggas, UN 1965, Gefahrnummer 23" in kontext.text
        assert "Tank 5 t im Hof" in kontext.text
        assert "SICHERHEITSKRITISCH - Hinweise: Dach mit Photovoltaik" in kontext.text
        assert "Nordtor" in kontext.text and "BMZ: Eingang Nord" in kontext.text
        assert "+436641234567" not in kontext.text  # keine Benachrichtigungsadressen
        assert kontext.gefahren and kontext.gefahren[0][0] == "Lagerhalle Muster"
    finally:
        db.close()


def test_unbestaetigte_oder_nicht_freigegebene_objekte_werden_ignoriert():
    db, incident_id = _setup(verknuepfung=OBJEKT_EINSATZ_VORSCHLAG)
    db2, incident2 = _setup(status=OBJEKT_STATUS_ENTWURF)
    try:
        assert not objekt_ki_kontext(db, incident_id)
        assert not objekt_ki_kontext(db2, incident2)
    finally:
        db.close()
        db2.close()


def test_ohne_objekt_bleibt_alles_leer():
    db, _ = _setup()
    try:
        kontext = objekt_ki_kontext(db, 999_999_999)
        assert not kontext and gefahren_hinweise(kontext) == [] and gefahren_auftraege(kontext) == []
    finally:
        db.close()


def test_deterministische_hinweise_und_auftraege_aus_objektgefahren():
    db, incident_id = _setup()
    try:
        kontext = objekt_ki_kontext(db, incident_id)
        (hinweis,) = gefahren_hinweise(kontext)
        assert hinweis.startswith("ACHTUNG Objekt Lagerhalle Muster:") and len(hinweis) <= 120
        (auftrag,) = gefahren_auftraege(kontext)
        assert auftrag["titel"].startswith("ACHTUNG Objektgefahren") and len(auftrag["titel"]) <= 60
        assert "Fluessiggas" in auftrag["detail"] and len(auftrag["detail"]) <= 120
    finally:
        db.close()


def test_objekt_ohne_gefahren_erzeugt_keine_achtung_eintraege():
    db, incident_id = _setup(mit_gefahr=False)
    try:
        kontext = objekt_ki_kontext(db, incident_id)
        assert kontext and gefahren_hinweise(kontext) == [] and gefahren_auftraege(kontext) == []
    finally:
        db.close()


@pytest.fixture()
def ai_enabled(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_DEFAULT", "claude-sonnet-4-6")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_FAST", "claude-haiku-4-5-20251001")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MAX_TOKENS", 1500)
    monkeypatch.setattr("app.services.ai_service.settings.AI_TIMEOUT", 20)


def _client(text):
    inhalt = MagicMock()
    inhalt.text = text
    antwort = MagicMock()
    antwort.content = [inhalt]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=antwort)
    return client


@pytest.mark.asyncio
async def test_prompt_enthaelt_objektinformationen_und_regeln(ai_enabled):
    from app.services.ai_service import generate_lage_hints, suggest_tasks

    db, incident_id = _setup()
    try:
        kontext = await lade_objekt_kontext(incident_id)
    finally:
        db.close()
    assert kontext

    client = _client('[{"titel":"Gas absperren"}]')
    with patch("anthropic.AsyncAnthropic", return_value=client):
        await suggest_tasks("Brand in Halle", "B2", objekt_kontext=kontext.text)
    nutzer = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "OBJEKTINFORMATIONEN" in nutzer and "Fluessiggas" in nutzer
    assert "erfinde nichts" in nutzer and "mit 'ACHTUNG' beginnen" in nutzer

    client = _client('["Zufahrt Nordtor"]')
    with patch("anthropic.AsyncAnthropic", return_value=client):
        await generate_lage_hints("Brand", "B2", "Hauptstrasse 5", objekt_kontext=kontext.text)
    assert "Nordtor" in client.messages.create.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_prompt_ohne_objekt_bleibt_unveraendert(ai_enabled):
    from app.services.ai_service import suggest_tasks

    client = _client("[]")
    with patch("anthropic.AsyncAnthropic", return_value=client):
        await suggest_tasks("Brand in Halle", "B2")
    assert client.messages.create.call_args.kwargs["messages"][0]["content"] == (
        "Alarmmeldung: Brand in Halle\nEinsatzart: B2"
    )


@pytest.mark.asyncio
async def test_hintergrundlauf_legt_gefahrenauftrag_auch_ohne_ki_antwort_an(monkeypatch):
    from app.routers import ui_incident

    db, incident_id = _setup()
    gesehen = {}

    async def fake_suggest(meldung, einsatzart, org_id=None, objekt_kontext=None):
        gesehen["kontext"] = objekt_kontext
        return []  # KI faellt aus

    monkeypatch.setattr("app.services.ai_service.suggest_tasks", fake_suggest)
    monkeypatch.setattr(ui_incident.manager, "broadcast", AsyncMock())
    try:
        await ui_incident._trigger_ai_task_suggestions(incident_id, "Brand", "B2", None)
        tasks = db.query(Task).filter(Task.incident_id == incident_id, Task.source == "ai_suggestion").all()
        assert len(tasks) == 1 and tasks[0].title.startswith("ACHTUNG Objektgefahren")
        assert "Fluessiggas" in (gesehen["kontext"] or "")
    finally:
        db.close()


def test_lagebild_kontext_enthaelt_objektinformationen():
    from app.services.incident_service import collect_situation_context

    db, incident_id = _setup()
    try:
        kontext = collect_situation_context(incident_id, db)
        assert "Fluessiggas" in kontext["objekt_informationen"]
        db2, ohne = _setup(verknuepfung=OBJEKT_EINSATZ_VORSCHLAG)
        try:
            assert "objekt_informationen" not in collect_situation_context(ohne, db2)
        finally:
            db2.close()
    finally:
        db.close()
