"""Worker-Races und atomarer Abschluss nach einem MariaDB-Lesekonflikt."""

from unittest.mock import Mock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.core.tenant import set_tenant_context
from app.models.master import OrgStorageUsage
from app.models.objekt import ObjektDokument, ObjektDokumentSeite
from app.models.user import User
from app.services import objekt_dokument_service as service
from tests.test_objekt_pr3 import _test_pdf
from tests.test_objekt_pr3 import pr3_env as pr3_env


def _upload(db, objekt):
    dokument = service.store_dokument_bytes(_test_pdf(2), "plan.pdf", objekt, None, db)
    db.commit()
    ident = dokument.id
    db.commit()
    return ident


def test_rendern_ohne_transaktion_und_doppelstart_ohne_zweite_verarbeitung(pr3_env, monkeypatch):
    import app.db as app_db

    db, org_id, _, objekt = pr3_env
    user = User(username="freigabe-race", display_name="Verwalter", password_hash="unused", org_id=org_id)
    db.add(user)
    db.commit()
    user_id = user.id
    ident = _upload(db, objekt)
    factory = app_db.SessionLocal
    sessions = []

    def session():
        result = factory()
        sessions.append(result)
        return result

    monkeypatch.setattr(app_db, "SessionLocal", session)
    zweiter_render = Mock(return_value=None)

    def render(*_):
        # Im Original oeffnet schon log_kontext=f"Dokument {dokument.id}"
        # nach commit() wieder einen Snapshot, noch vor dem ersten Rendering.
        assert all(not s.in_transaction() for s in sessions)
        service.verarbeite_dokument(ident, render_func=zweiter_render)
        # Konkreter paralleler Schreibpfad: Die Freigabe-Route laesst bereits
        # waehrend der Verarbeitung freigeben (prueft nur freigabe_status).
        # Sie committet dieselbe Dokumentzeile in einer anderen Session.
        with factory() as andere:
            set_tenant_context(andere, org_id)
            anderes_dokument = andere.get(ObjektDokument, ident)
            if anderes_dokument.freigabe_status == "wartet_freigabe":
                service.gebe_dokument_frei(andere, anderes_dokument, user_id)
                andere.commit()
        return None

    service.verarbeite_dokument(ident, render_func=render)
    zweiter_render.assert_not_called()
    db.expire_all()
    dokument = db.get(ObjektDokument, ident)
    assert dokument.status == "fertig", dokument.fehler_text
    assert dokument.freigabe_status == "freigegeben" and dokument.ist_aktuelle_version
    assert dokument.freigegeben_von_id == user_id
    assert db.query(ObjektDokumentSeite).filter_by(dokument_id=ident).count() == 2
    assert db.get(OrgStorageUsage, org_id).used_bytes == dokument.belegt_bytes
    belegt = dokument.belegt_bytes
    service.verarbeite_dokument(ident, render_func=zweiter_render)
    zweiter_render.assert_not_called()
    db.expire_all()
    assert db.get(OrgStorageUsage, org_id).used_bytes == belegt


def test_doppelstart_waehrend_rendern_reserviert_quota_nur_einmal(pr3_env):
    db, org_id, _, objekt = pr3_env
    ident = _upload(db, objekt)
    zweiter_render = Mock(return_value=None)

    def render(*_):
        service.verarbeite_dokument(ident, render_func=zweiter_render)
        return None

    service.verarbeite_dokument(ident, render_func=render)
    zweiter_render.assert_not_called()
    db.expire_all()
    dokument = db.get(ObjektDokument, ident)
    assert dokument.status == "fertig", dokument.fehler_text
    assert db.query(ObjektDokumentSeite).filter_by(dokument_id=ident).count() == 2
    assert db.get(OrgStorageUsage, org_id).used_bytes == dokument.belegt_bytes


@pytest.mark.parametrize("phase,errno,konflikte,status", [
    ("verarbeitung", 1020, 1, "fertig"), ("verarbeitung", 1020, 2, "fehler"),
    ("fertig", 1020, 1, "fertig"),
    ("fertig", 1020, 2, "fehler"), ("fertig", 9999, 1, "fehler"),
])
def test_abschluss_retry_mit_frischer_session_und_ohne_doppelte_quota(
    pr3_env, monkeypatch, phase, errno, konflikte, status,
):
    import app.db as app_db

    db, org_id, _, objekt = pr3_env
    ident = _upload(db, objekt)
    engine = db.get_bind()
    factory = app_db.SessionLocal
    sessions = []
    versuche = []

    def session():
        result = factory()
        sessions.append(result)
        return result

    def konflikt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE objekt_dokument SET") and phase in parameters:
            versuche.append(sessions[-1])
            if len(versuche) <= konflikte:
                raise OperationalError(statement, parameters, Exception(errno, "Record has changed since last read"))

    monkeypatch.setattr(app_db, "SessionLocal", session)
    render = Mock(return_value=None)
    event.listen(engine, "before_cursor_execute", konflikt)
    try:
        service.verarbeite_dokument(ident, render_func=render)
    finally:
        event.remove(engine, "before_cursor_execute", konflikt)
    assert render.call_count == (0 if phase == "verarbeitung" and status == "fehler" else 2)
    assert len(versuche) == (2 if errno == 1020 else 1)
    assert len(set(versuche)) == len(versuche)
    db.expire_all()
    dokument = db.get(ObjektDokument, ident)
    assert dokument.status == status, dokument.fehler_text
    assert db.get(OrgStorageUsage, org_id).used_bytes == dokument.belegt_bytes
    assert db.query(ObjektDokumentSeite).filter_by(dokument_id=ident).count() == (2 if status == "fertig" else 0)
    if status == "fehler":
        assert list(service.absolute_pfad(dokument.pfad).parent.iterdir()) == [service.absolute_pfad(dokument.pfad)]
        # Ein bereits fehlgeschlagenes Dokument kann erfolgreich nachgeholt werden.
        service.verarbeite_dokument(ident, render_func=lambda *_: None)
        db.expire_all()
        assert db.get(ObjektDokument, ident).status == "fertig"
        assert db.get(OrgStorageUsage, org_id).used_bytes == db.get(ObjektDokument, ident).belegt_bytes
