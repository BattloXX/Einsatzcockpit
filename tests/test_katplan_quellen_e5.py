"""E5: Quellen bis zur PDF-Seite in UI, Druck, QS und MCP."""

import re
from datetime import date

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan
from app.models.user import User
from app.services import katplan_qs_service as qs
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_mcp_service import fassung_laden
from app.services.katplan_pdf_service import render_katplan_html
from app.services.katplan_quelle_service import quellen_seiten
from app.services.katplan_service import lege_element_an
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, umgebung  # noqa: F401
from tests.test_katplan_k2a import basis


@pytest.fixture
def quelle(umgebung, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    (tmp_path / "original.pdf").write_bytes(b"%PDF-1.4\n")
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = db.query(User).filter_by(username=umgebung["users"]["katplan_freigeber"]).one()
        d = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="E5 Handbuch",
                            herausgeber="Gemeinde", stand=date(2026, 10, 21),
                            dateiname_original="original.pdf", pfad="original.pdf", groesse_bytes=10,
                            seitenzahl=14, status="fertig", ist_aktuelle_version=True)
        db.add(d)
        db.flush()
        for nr in range(1, 15):
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=d.id,
                                       seiten_nr=nr, einzel_pdf_pfad="original.pdf"))
        ids = [lege_element_an(db, plan, user, typ="massnahme", titel=f"Belegte Maßnahme {n}",
                              quelle_dokument_id=d.id, quelle_seiten="13-14").id for n in range(2)]
        version = freigeben(db, plan, user)
        db.commit()
        return {**umgebung, "dok_id": d.id, "uuid": d.uuid, "vid": version.id, "element_ids": ids}


def test_chip_vorschau_original_arbeitsstand_und_leseversion(client, quelle):
    login(client, quelle)
    prefix = f'/plaene/quelle/{quelle["uuid"]}'
    for suffix, query in (("?ansicht=arbeitsstand", ""), ("", f'?version_id={quelle["vid"]}')):
        response = client.get(basis(quelle) + suffix)
        assert response.status_code == 200
        assert 'class="kp-chip" title="E5 Handbuch (v1), S. 13-14"' in response.text
        assert re.search(r">\s*S\. 13-14</a>", response.text)
        article = re.search(r"<article\b.*?</article>", response.text, re.S)[0]
        assert article.count('loading="lazy"') == 2
        for nr in (13, 14):
            assert article.count(f'src="{prefix}/seite/{nr}/thumb{query}"') == 1
            assert f'alt="Seite {nr}"' in response.text
        assert f'{prefix}/original{query}#page=13' in response.text
    login(client, quelle, "readonly")
    page = client.get(basis(quelle))
    url = f'{prefix}/original?version_id={quelle["vid"]}'
    assert url + "#page=13" in page.text
    assert client.get(url).status_code == 200


def test_ungueltige_seiten_und_vorschau_limit(client, quelle):
    with TestingSession() as db:
        set_tenant_context(db, None)
        d = db.get(KatplanDokument, quelle["dok_id"])
        assert len(quellen_seiten(d, "1-14", limit=4)) == 4
        with pytest.raises(ValueError):
            quellen_seiten(d, "1-14,kaputt", limit=4)
        for eid in quelle["element_ids"]:
            db.get(KatplanElement, eid).quelle_seiten = "kaputt"
        db.commit()
    login(client, quelle)
    response = client.get(basis(quelle) + "?ansicht=arbeitsstand")
    assert response.status_code == 200
    assert re.search(r">\s*S\. kaputt</a>", response.text)
    assert f'/quelle/{quelle["uuid"]}/seite/' not in response.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, quelle["plan_id"])
        user = db.query(User).filter_by(username=quelle["users"]["katplan_verwalter"]).one()
        _, elements, _, _ = fassung_laden(db, user, plan, "arbeitsstand")
        assert all(not e["source_references"][0]["seiten_liste"] for e in elements)


def test_pdf_fussnoten_verzeichnis_und_mcp_original(quelle):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, quelle["plan_id"])
        user = db.query(User).filter_by(username=quelle["users"]["readonly"]).one()
        html = render_katplan_html(db, user, plan)
        assert html.count("<sup>[1]</sup>") == 2
        assert "<sup>[2]</sup>" not in html
        assert "Quellenverzeichnis" in html
        assert html.count("[1] E5 Handbuch, Version 1, Gemeinde, Stand 2026-10-21, Seiten 13-14") == 1
        assert '<img' not in html
        _, elements, _, _ = fassung_laden(db, user, plan, "freigegeben")
        ref = elements[0]["source_references"][0]
        assert ref["original_url"] == f'/plaene/quelle/{quelle["uuid"]}/original?version_id={quelle["vid"]}#page=13'
        assert [s["seiten_nr"] for s in ref["seiten_liste"]] == [13, 14]
        # Ältere, weiterhin referenzierte Dokumente fehlen im aktuellen Quellenkatalog.
        db.get(KatplanDokument, quelle["dok_id"]).ist_aktuelle_version = False
        db.flush()
        editor = db.query(User).filter_by(username=quelle["users"]["katplan_verwalter"]).one()
        html = render_katplan_html(db, editor, plan, entwurf=True)
        assert "Gemeinde, Stand 2026-10-21, Seiten 13-14" in html


def test_qs_quelle_fehlt_nur_massnahmen_mit_aktuellen_quellen(umgebung):  # noqa: F811
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = db.query(User).filter_by(username=umgebung["users"]["katplan_verwalter"]).one()
        e = lege_element_an(db, plan, user, typ="massnahme", titel="Ohne Beleg")
        heute = date(2026, 10, 21)
        assert "quelle_fehlt" not in qs.plan_zusammenfassung(db, plan, heute)
        d = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Quelle", dateiname_original="q.pdf",
                            pfad="q.pdf", groesse_bytes=1, seitenzahl=1, ist_aktuelle_version=True)
        db.add(d)
        db.flush()
        assert qs.plan_zusammenfassung(db, plan, heute)["quelle_fehlt"] == 1
        e.typ = "info"
        db.flush()
        assert "quelle_fehlt" not in qs.plan_zusammenfassung(db, plan, heute)
        e.typ = "massnahme"
        e.quelle_dokument_id = d.id
        db.flush()
        assert "quelle_fehlt" not in qs.plan_zusammenfassung(db, plan, heute)
        e.quelle_dokument_id = None
        for feld in ("archiviert", "ist_aktuelle_version"):
            d.archiviert, d.ist_aktuelle_version = feld == "archiviert", feld != "ist_aktuelle_version"
            db.flush()
            assert "quelle_fehlt" not in qs.plan_zusammenfassung(db, plan, heute)


def test_seitenwahl_rechte_und_editor(client, quelle):
    login(client, quelle)
    url = basis(quelle) + f'/quellen/{quelle["dok_id"]}/seitenwahl'
    response = client.get(url)
    assert response.status_code == 200
    assert response.text.count('loading="lazy"') == 14
    assert "$event.shiftKey" in response.text and "ergänzen" in response.text
    editor = client.get(basis(quelle) + f'/bearbeiten?element={quelle["element_ids"][0]}')
    assert editor.status_code == 200
    assert "Seite wählen" in editor.text and 'name="quelle_seiten"' in editor.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        other = KatplanPlan(org_id=quelle["org_id"], titel="Anderer Plan")
        db.add(other)
        db.commit()
        assert client.get(f'/plaene/{other.id}/quellen/{quelle["dok_id"]}/seitenwahl').status_code == 404
    login(client, quelle, "readonly")
    assert client.get(url).status_code == 403
    login(client, quelle, "fremd")
    assert client.get(url).status_code == 404
