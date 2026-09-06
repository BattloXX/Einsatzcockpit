"""Systemvorlage der Migration 0233: Inhalt, Idempotenz und Snapshot."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.probenplanung import (
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateSection,
    ProbeCheckliste,
    ProbeChecklistSection,
)
from app.models.teilnahme import Termin
from app.services.probe_checklist_service import snapshot_erzeugen


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0233_add_vollprobe_checklist_template.py"
    spec = importlib.util.spec_from_file_location("migration_0233", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _insert_org(conn, org_id: int) -> None:
    conn.execute(sa.text(
        "INSERT INTO fire_dept (id,name,slug,color,bos,withdraw_press_factor,withdraw_press_reserve,"
        "escalation_grace_min,is_home_org,is_active,short_code,timezone,created_at) "
        "VALUES (:id,'Test','test-vollprobe','#123456','Feuerwehr',0.5,10,3,0,1,'TST','Europe/Vienna','2026-01-01')"
    ), {"id": org_id})
    conn.execute(sa.text(
        "INSERT INTO probeart (org_id,name,kurz,farbe,sortierung,aktiv,termin_typ,checkliste_erforderlich,"
        "teilnahme_erforderlich,nachbereitung_erforderlich,uebungseinsatz_erlaubt) "
        "VALUES (:id,'Vollprobe','VP','#dc2626',10,1,'uebung',0,1,0,0)"
    ), {"id": org_id})


def test_vollprobe_systemvorlage_idempotent_und_snapshotfaehig(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0233.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        _insert_org(conn, 9002)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        assert conn.execute(sa.text(
            "SELECT count(*) FROM checklist_template WHERE org_id=9002 AND code='vollprobe_checklist'"
        )).scalar() == 1
        assert conn.execute(sa.text(
            "SELECT checklist_template_id FROM probeart WHERE org_id=9002 AND name='Vollprobe'"
        )).scalar() is not None

    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    try:
        template = session.query(ChecklistTemplate).filter_by(org_id=9002, code="vollprobe_checklist").one()
        assert template.name == "Vollprobe – Checkliste"
        assert template.aktive_version_id is not None
        sections = session.query(ChecklistTemplateSection).filter_by(org_id=9002, version_id=template.aktive_version_id).order_by(ChecklistTemplateSection.sortierung).all()
        assert [section.titel for section in sections] == [
            "Allgemeine Angaben", "Eigentümer und Bewohner", "Verletzte / Feuerwehrjugend",
            "Externe Personen / Kantine", "Eingesetzte Hilfsmittel", "Verletzte", "Pläne",
            "Aufgabenvorstellung", "Besondere Aufgaben", "Infos an die Dienstgrade", "Alarmierung",
            "Besonderheiten und Gefahren", "Skizze",
        ]
        items = session.query(ChecklistTemplateItem).filter(ChecklistTemplateItem.section_id.in_([s.id for s in sections])).all()
        by_title = {item.titel: item for item in items}
        for title in (
            "Vereinbart mit Eigentümer", "Bewohner verständigt", "Getroffene Sondervereinbarungen",
            "Jugendleiter verständigt", "Kantine verständigt", "Eingesetzte Hilfsmittel",
            "Verletzte mit Warnweste markiert", "Pläne vorhanden", "Aufgabenvorstellung", "Besondere Aufgaben",
            "Infos an die Dienstgrade", "Alarmtext", "Besonderes", "Skizze",
        ):
            assert title in by_title
        assert {"Fahnen", "Blitzleuchte", "Nebelmaschine", "Farbnebel"}.issubset(set(json.loads(by_title["Eingesetzte Hilfsmittel"].optionen)))
        assert {"Brandschutzplan", "Übungsskizze"}.issubset(set(json.loads(by_title["Pläne vorhanden"].optionen)))

        termin = Termin(org_id=9002, typ="uebung", titel="Vollprobe Test", beginn=datetime(2026, 6, 1, 19, 0), probeart_id=session.execute(sa.text("SELECT id FROM probeart WHERE org_id=9002 AND name='Vollprobe'")).scalar_one())
        session.add(termin)
        session.flush()
        snapshot = snapshot_erzeugen(session, termin, SimpleNamespace(timezone="Europe/Vienna"))
        assert snapshot is not None
        assert snapshot.template_id == template.id
        copied = session.query(ProbeCheckliste).filter_by(termin_id=termin.id).one()
        copied_sections = session.query(ProbeChecklistSection).filter_by(checkliste_id=copied.id).order_by(ProbeChecklistSection.sortierung).all()
        assert [section.titel for section in copied_sections] == [section.titel for section in sections]
        for source, copied_section in zip(sections, copied_sections, strict=True):
            source_titles = [item.titel for item in session.query(ChecklistTemplateItem).filter_by(section_id=source.id).order_by(ChecklistTemplateItem.sortierung)]
            copied_titles = [item.titel for item in copied.items if item.section_id == copied_section.id]
            assert copied_titles == source_titles
        copied.items[0].wert_text = "Nur in der Probe"
        assert by_title["Namen der Ausführenden"].titel == "Namen der Ausführenden"
    finally:
        session.close()
