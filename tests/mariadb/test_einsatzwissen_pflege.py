"""Referenzzählung und Konfliktschutz auf einer migrierten MariaDB."""

import io
import os
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAnhang, KatplanPlan
from app.models.master import FireDept, OrgSettings
from app.models.user import Role, User, UserRole
from app.services import katplan_anhang_service as service
from app.services.katplan_service import KatplanKonflikt
from app.services.storage_service import reconcile_storage


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
def test_geteilte_datei_und_konflikt_mariadb(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    org_id = plan_id = user_id = None
    try:
        with Session(engine) as db:
            set_tenant_context(db, None)
            suffix = uuid4().hex
            org = FireDept(slug="wissen-pflege-" + suffix, name="Wissenspflege Test")
            db.add(org)
            db.flush()
            org_id = org.id
            db.add(OrgSettings(org_id=org.id, katplan_modul_aktiv=True))
            role = db.query(Role).filter_by(code="katplan_verwalter").first()
            if role is None:
                role = Role(code="katplan_verwalter", label="Planverwaltung")
                db.add(role)
                db.flush()
            user = User(username="wissen-" + suffix, display_name="Test", org_id=org.id)
            db.add(user)
            db.flush()
            user_id = user.id
            db.add(UserRole(user_id=user.id, role_id=role.id))
            plan = KatplanPlan(org_id=org.id, titel="Referenztest")
            db.add(plan)
            db.flush()
            plan_id = plan.id
            pdf = PdfWriter()
            pdf.add_blank_page(width=200, height=300)
            raw = io.BytesIO()
            pdf.write(raw)
            original = service.anhang_hochladen(db, plan, user, raw.getvalue(), "original.pdf", "application/pdf")
            doppelt = service.anhang_hochladen(db, plan, user, raw.getvalue(), "zweiter.pdf", "application/pdf")
            assert original.pfad == doppelt.pfad
            assert reconcile_storage(db, org.id) == len(raw.getvalue())
            ident, uid, version = original.id, original.uuid, plan.version
            db.commit()
        with Session(engine) as erster, Session(engine) as zweiter:
            set_tenant_context(erster, org_id)
            set_tenant_context(zweiter, org_id)
            p1, p2 = erster.get(KatplanPlan, plan_id), zweiter.get(KatplanPlan, plan_id)
            u1, u2 = erster.get(User, user_id), zweiter.get(User, user_id)
            service.pflege_version(erster, p1, u1, version)
            referenz = service.dokument_referenzieren(erster, p1, u1, "anhang:" + uid)
            assert referenz.pfad == erster.get(KatplanAnhang, ident).pfad
            erster.commit()
            with pytest.raises(KatplanKonflikt):
                service.pflege_version(zweiter, p2, u2, version)
            zweiter.rollback()
    finally:
        with Session(engine) as db:
            set_tenant_context(db, None)
            for model, ident in ((KatplanPlan, plan_id), (User, user_id), (FireDept, org_id)):
                if ident is not None:
                    db.execute(delete(model).where(model.id == ident))
            db.commit()
        engine.dispose()
