"""Objektverwaltung PR 8: KI-Klassifizierung (Vision, Review-Queue, Opt-in)."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    KI_VORSCHLAG_OFFEN,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektSeiteKiVorschlag,
)
from app.services.objekt_ki_service import _parse_antwort, ki_klassifikation_enabled


# ── Antwort-Parser ────────────────────────────────────────────────────────────

def test_parse_gueltiges_json():
    antwort = ('{"dokumentart": "bma_melderplan", "titel": "Melderplan EG Nord", '
               '"melderlinien": "12, 13", "stand": "2025-09-01", "begruendung": "Grundriss mit Meldern"}')
    p = _parse_antwort(antwort)
    assert p["dokumentart"] == "bma_melderplan"
    assert p["titel"] == "Melderplan EG Nord"
    assert p["melderlinien"] == "12, 13"
    assert p["stand"] is not None and p["stand"].year == 2025


def test_parse_markdown_fence_und_unbekannter_code():
    antwort = '```json\n{"dokumentart": "phantasie_art", "titel": null, "begruendung": "unsicher"}\n```'
    p = _parse_antwort(antwort)
    assert p is not None
    assert p["dokumentart"] is None  # unbekannter Code → verworfen


def test_parse_ungueltiges_json():
    assert _parse_antwort("Das ist kein JSON") is None
    assert _parse_antwort('["liste", "statt", "objekt"]') is None


# ── complete_vision: Quota/BYOK-Verhalten (gemockt) ───────────────────────────

def test_complete_vision_quota_ueberschritten():
    from app.services.ai_service import AIServiceError, complete_vision
    with patch("app.services.ai_service._get_ai_cfg",
               return_value={"enabled": True, "api_key": "sk-x", "model_fast": "m",
                             "model_default": "m", "max_tokens": 100, "timeout": 5}), \
         patch("app.services.ai_service._get_org_ai_cfg",
               return_value={"ai_mode": "central", "ai_api_key_enc": None,
                             "ai_monthly_token_quota": 100, "ai_tokens_used_month": 100}):
        with pytest.raises(AIServiceError, match="Monatskontingent"):
            asyncio.run(complete_vision("sys", "user", [b"png"], org_id=1))


def test_complete_vision_deaktiviert():
    from app.services.ai_service import AIServiceError, complete_vision
    with patch("app.services.ai_service._get_ai_cfg",
               return_value={"enabled": False, "api_key": "", "model_fast": "m",
                             "model_default": "m", "max_tokens": 100, "timeout": 5}):
        with pytest.raises(AIServiceError, match="nicht aktiviert"):
            asyncio.run(complete_vision("sys", "user", [b"png"], org_id=None))


# ── Opt-in-Gate + Review-Statusmaschine ───────────────────────────────────────

@pytest.fixture()
def ki_db(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekt_media"))

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="ki-org", name="KI Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, objekt_ki_klassifikation_enabled=True))
    objekt = Objekt(org_id=org.id, nummer=1, name="KI-Testobjekt",
                    status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.flush()
    dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id,
                              dateiname_original="t.pdf", pfad="x/t/original.pdf",
                              seitenzahl=1, status="fertig")
    db.add(dokument)
    db.flush()
    seite = ObjektDokumentSeite(org_id=org.id, objekt_id=objekt.id,
                                dokument_id=dokument.id, seiten_nr=1)
    db.add(seite)
    db.commit()

    yield db, org, objekt, seite

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_optin_gate(ki_db):
    db, org, _, _ = ki_db
    with patch("app.services.ai_service.is_enabled", return_value=True):
        assert ki_klassifikation_enabled(org.id, db) is True
    with patch("app.services.ai_service.is_enabled", return_value=False):
        assert ki_klassifikation_enabled(org.id, db) is False
    # Org-Flag aus → False trotz aktivem KI-Dienst
    settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    settings_row.objekt_ki_klassifikation_enabled = False
    db.commit()
    with patch("app.services.ai_service.is_enabled", return_value=True):
        assert ki_klassifikation_enabled(org.id, db) is False
    assert ki_klassifikation_enabled(None, db) is False


def test_analysiere_seite_erzeugt_vorschlag(ki_db, tmp_path):
    from app.services import objekt_ki_service
    db, org, objekt, seite = ki_db

    # Bild-Datei anlegen (echtes PNG via Pillow)
    import io

    from PIL import Image
    from app.services.objekt_dokument_service import _storage_root
    bild_dir = _storage_root() / "x" / "t"
    bild_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (40, 60), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (bild_dir / "seite_0001.png").write_bytes(buf.getvalue())
    seite.bild_pfad = "x/t/seite_0001.png"
    db.commit()

    async def fake_vision(system, user, images, **kw):
        assert images and images[0][:8] == b"\x89PNG\r\n\x1a\n"
        return ('{"dokumentart": "brandschutzplan", "titel": "BSP EG", '
                '"begruendung": "Plan mit Symbolen"}')

    with patch.object(objekt_ki_service, "analysiere_seite", wraps=objekt_ki_service.analysiere_seite), \
         patch("app.services.ai_service.complete_vision", side_effect=fake_vision):
        vorschlag = asyncio.run(objekt_ki_service.analysiere_seite(seite, db))
    db.commit()

    assert vorschlag is not None
    assert vorschlag.dokumentart == "brandschutzplan"
    assert vorschlag.status == KI_VORSCHLAG_OFFEN
    # Seite selbst bleibt UNKLASSIFIZIERT (nie Auto-Apply)
    db.refresh(seite)
    assert seite.dokumentart is None


def test_ki_fehler_liefert_none(ki_db):
    from app.services import objekt_ki_service
    from app.services.ai_service import AIServiceError
    db, _, _, seite = ki_db
    seite.bild_pfad = None
    seite.thumb_pfad = None
    # Ohne Rendering → None ohne KI-Aufruf
    assert asyncio.run(objekt_ki_service.analysiere_seite(seite, db)) is None


def test_pr8_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "objekt_seite_ki_vorschlag" in _TENANT_TABLE_NAMES
    from app.routers.ui_objekt_dokumente import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/{objekt_id}/dokumente/ki-analyse" in pfade
    assert "/objekte/{objekt_id}/dokumente/ki-review/{vorschlag_id}/uebernehmen" in pfade
    assert "/objekte/{objekt_id}/dokumente/ki-review/alle-uebernehmen" in pfade
