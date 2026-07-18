"""PR 1: tenant-gescopter Org-Export (Collector, Isolation, Redaktion, Medien)."""
import json
import zipfile

import pytest

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept, Member
from app.models.sso import OrgSsoConfig
from app.services import org_export_media as oem
from app.services import org_export_service as oes
from tests.conftest import TestingSession


@pytest.fixture
def db():
    s = TestingSession()
    set_tenant_context(s, None)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _org(db, name: str) -> FireDept:
    o = FireDept(slug=name.lower().replace(" ", "-"), name=name)
    db.add(o)
    db.flush()
    return o


# ── Klassifikation / Coverage (reine Metadata) ────────────────────────────────

def test_scope_rules_und_exclude_disjunkt():
    assert set(oes.scope_rules()) & oes.EXCLUDE_TABLES == set()


def test_jede_tabelle_ist_klassifiziert():
    """Jede nicht ausgeschlossene Tabelle ist Root (Org-Spalte) ODER Kind (>=1 FK).

    Eine neue Tabelle ohne Klassifikation waere nicht exportierbar -> Test rot.
    """
    rules = oes.scope_rules()
    orphan = []
    for t in Base.metadata.tables.values():
        if t.name in oes.EXCLUDE_TABLES:
            continue
        if (t.name not in rules and not list(t.foreign_keys)
                and t.name not in oes._EXTRA_LINKS):
            orphan.append(t.name)
    assert orphan == [], f"Unklassifizierte Tabellen (weder Root noch Kind): {orphan}"


# ── Tenant-Isolation + Manifest ───────────────────────────────────────────────

def test_export_enthaelt_nur_eigene_org(db, tmp_path):
    a = _org(db, "Export A")
    b = _org(db, "Export B")
    db.add(Member(org_id=a.id, lastname="Alpha", firstname="Anna"))
    db.add(Member(org_id=a.id, lastname="Alpha", firstname="Amy"))
    db.add(Member(org_id=b.id, lastname="Beta", firstname="Bob"))
    db.commit()

    ziel = oes.export_org(db, a.id, tmp_path, include_media=False)
    with zipfile.ZipFile(ziel) as zf:
        member_rows = [json.loads(x) for x in
                       zf.read("data/member.jsonl").decode("utf-8").splitlines() if x]
        manifest = json.loads(zf.read("manifest.json"))

    assert {m["org_id"] for m in member_rows} == {a.id}   # KEINE Fremd-Org
    assert manifest["tables"]["member"] == len(member_rows) == 2
    assert manifest["org_id"] == a.id
    assert manifest["format_version"] == oes.FORMAT_VERSION


# ── Secret-Redaktion (*_enc) ──────────────────────────────────────────────────

def test_secret_spalten_werden_redigiert(db, tmp_path):
    a = _org(db, "Redact Org")
    db.add(OrgSsoConfig(org_id=a.id, client_secret_enc=encrypt_secret("supersecret")))
    db.commit()

    ziel = oes.export_org(db, a.id, tmp_path, include_media=False)
    with zipfile.ZipFile(ziel) as zf:
        rows = [json.loads(x) for x in
                zf.read("data/org_sso_config.jsonl").decode("utf-8").splitlines() if x]
    assert rows
    assert all(r["client_secret_enc"] is None for r in rows)   # nicht exportiert


# ── Medien-Referenzen ─────────────────────────────────────────────────────────

def test_medien_referenzen_relativ(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", "app_storage/incident_media")
    refs = oem.medien_referenzen(
        "task_media", [{"id": 5, "storage_path": "1/2/x.jpg", "thumb_path": "1/2/x_thumb.jpg"}])
    arcs = {a for a, _ in refs}
    assert "task_media/5/storage_path/x.jpg" in arcs
    assert "task_media/5/thumb_path/x_thumb.jpg" in arcs
    assert any(str(p).replace("\\", "/").endswith("incident_media/1/2/x.jpg") for _, p in refs)


def test_medien_referenzen_zusammengesetzt():
    refs = oem.medien_referenzen(
        "site_media", [{"id": 9, "org_id": 3, "incident_site_id": 7, "stored_filename": "foto.jpg"}])
    assert refs
    arcname, pfad = refs[0]
    assert arcname == "site_media/9/stored_filename/foto.jpg"
    assert str(pfad).replace("\\", "/").endswith("lage_media/3/7/foto.jpg")


def test_medien_referenzen_unbekannte_tabelle():
    assert oem.medien_referenzen("member", [{"id": 1}]) == []


# ── Partielles Backup (Bereiche) ──────────────────────────────────────────────

def test_area_roots_sind_echte_roots():
    rules = oes.scope_rules()
    for area, tabellen in oes.AREA_ROOTS.items():
        for t in tabellen:
            assert t in rules, f"AREA_ROOTS[{area}] enthaelt Nicht-Root {t}"


def test_areas_aus_string():
    assert oes.areas_aus_string(None) is None            # vollstaendig
    assert oes.areas_aus_string("") == set()              # nur Kern
    assert oes.areas_aus_string("objekte, mannschaft") == {"objekte", "mannschaft"}


def test_partieller_export_laesst_bereich_weg(db, tmp_path):
    a = _org(db, "Partial Org")
    db.add(Member(org_id=a.id, lastname="Mann", firstname="M"))
    from app.models.objekt import Objekt
    db.add(Objekt(org_id=a.id, nummer="P-1", name="Haus P"))
    db.commit()

    # Nur Mannschaft -> Objekt fehlt, Member da
    ziel = oes.export_org(db, a.id, tmp_path, include_media=False, areas={"mannschaft"})
    with zipfile.ZipFile(ziel) as zf:
        namen = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert "data/member.jsonl" in namen
    assert "data/objekt.jsonl" not in namen
    assert manifest["areas"] == ["mannschaft"]

    # Vollstaendig -> Objekt enthalten
    ziel2 = oes.export_org(db, a.id, tmp_path, include_media=False, areas=None)
    with zipfile.ZipFile(ziel2) as zf:
        assert "data/objekt.jsonl" in set(zf.namelist())
