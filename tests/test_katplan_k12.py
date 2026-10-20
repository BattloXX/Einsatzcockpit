"""K12: dynamische QS, rechtegepruefte Offlinefassung und Quellenvergleich."""

import io
from datetime import date, timedelta

import pytest
from pypdf import PdfReader

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAenderung, KatplanAnhang, KatplanDokument, KatplanDokumentSeite, KatplanElement,
    KatplanLink, KatplanPlan, KatplanVersion,
)
from app.models.kontakt import Kontakt
from app.models.user import User
from app.services import katplan_qs_service as qs
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_pdf_service import render_katplan_html, render_katplan_pdf
from app.services.katplan_quelle_service import vergleiche_quellen
from app.services.katplan_service import lege_element_an, markiere_ueberarbeitung, protokolliere
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_katplan import inhalte  # noqa: F401

HEUTE = date(2026, 10, 20)


def _user(db, info, rolle="katplan_freigeber"):
    return db.query(User).filter_by(username=info["users"][rolle]).one()


def _dokument(db, plan, nummer=1, gruppe=None):
    d = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel=f"Quelle v{nummer}",
                        dateiname_original="quelle.pdf", pfad="quelle.pdf", groesse_bytes=10,
                        seitenzahl=4, status="fertig", versionsnummer=nummer,
                        dokument_gruppe_id=gruppe, ist_aktuelle_version=nummer > 1)
    db.add(d)
    db.flush()
    return d


@pytest.mark.parametrize("code", list(qs.LABELS))
def test_jeder_qs_code_einzeln(umgebung, code, monkeypatch):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = _user(db, umgebung)
        e = lege_element_an(db, plan, user, typ="info", titel="Test")
        if code == "ohne_geometrie":
            e.typ = "kontrollstelle"
        elif code == "geo_ungeprueft":
            e.geo_status = "ungeprueft"
        elif code == "link_ziel_fehlt":
            db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=e.id,
                              ziel_typ="kontakt", ziel_id=9999999, rolle="zustaendig"))
        elif code == "telefon_im_text":
            kontakt = Kontakt(org_id=plan.org_id, anzeigename="Kontakt")
            db.add(kontakt)
            db.flush()
            db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=e.id,
                              ziel_typ="kontakt", ziel_id=kontakt.id, rolle="info"))
            e.beschreibung_html = "<p>Telefon +43 555 123456</p>"
            monkeypatch.setattr(qs, "ziel_verfuegbar", lambda *args: True)
        elif code == "kontakt_geaendert":
            kontakt = Kontakt(org_id=plan.org_id, anzeigename="Alter Name")
            db.add(kontakt)
            db.flush()
            db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=e.id,
                              ziel_typ="kontakt", ziel_id=kontakt.id, rolle="zustaendig"))
            db.flush()
            freigeben(db, plan, user)
            kontakt.anzeigename = "Neuer Name"
            monkeypatch.setattr(qs, "ziel_verfuegbar", lambda *args: True)
        elif code == "pruefen_faellig":
            e.pruefen_am = HEUTE
        elif code == "gueltig_abgelaufen":
            e.gueltig_bis = HEUTE - timedelta(days=1)
        elif code == "ki_ungeprueft":
            e.herkunft = "ai_suggested"
        elif code == "quelle_neuer":
            alt = _dokument(db, plan)
            _dokument(db, plan, 2, alt.id)
            e.quelle_dokument_id = alt.id
        db.flush()
        hinweise = qs.element_hinweise(db, plan, e, heute=HEUTE)
        assert hinweise == [{"code": code, "text": qs.LABELS[code], "farbe": "amber"}]


def test_qs_grenzen_summen_freigabe_und_snapshot(umgebung):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = _user(db, umgebung)
        for titel in ("A", "B"):
            e = lege_element_an(db, plan, user, typ="kontrollstelle", titel=titel)
            e.herkunft = "ai_suggested"
            e.gueltig_bis = HEUTE
            e.pruefen_am = HEUTE + timedelta(days=1)
        db.flush()
        assert qs.plan_zusammenfassung(db, plan, HEUTE) == {"ohne_geometrie": 2, "ki_ungeprueft": 2}
        version = freigeben(db, plan, user)
        db.flush()
        assert plan.aktuelle_version_id == version.id
        assert "qs_" not in version.plan_json and "hinweise" not in version.plan_json
        assert qs.plan_zusammenfassung(db, plan, HEUTE)["ki_ungeprueft"] == 2


def test_ki_nur_ui_speichern_bestaetigt_und_ui_zeigt_qs(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        element = db.get(KatplanElement, e.id)
        element.herkunft = "ai_suggested"
        protokolliere(db, plan, "element", "geaendert", _user(db, umgebung),
                      element_id=e.id, quelle="mcp", feld="titel", neu="MCP")
        db.commit()
        assert "ki_ungeprueft" in {h["code"] for h in qs.element_hinweise(db, plan, element, heute=HEUTE)}
    page = client.get(basis(umgebung) + f"/bearbeiten?element={e.id}")
    assert page.status_code == 200 and "KI ungeprüft" in page.text and "Qualität:" in page.text
    assert "2 Hinweise" in client.get("/katastrophenplaene").text
    response = post(client, basis(umgebung) + f"/elemente/{e.id}", **element_form(e))
    assert response.status_code == 200
    assert 'id="kp-qualitaet"' in response.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        element = db.get(KatplanElement, e.id)
        assert "ki_ungeprueft" not in {h["code"] for h in qs.element_hinweise(db, plan, element, heute=HEUTE)}
    login(client, umgebung, "katplan_freigeber")
    assert "offene Hinweise" in client.get(basis(umgebung)).text
    assert post(client, basis(umgebung) + "/freigeben").status_code == 200


def test_pdf_html_rechte_snapshot_und_download(client, inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        db.get(KatplanElement, inhalte["element_id"]).titel = "Nur neuer Arbeitsstand"
        db.commit()
        html = render_katplan_html(db, _user(db, inhalte, "readonly"), plan)
        for text in ("Wasser Brücke", "Offener Inhalt", "Freigegebene Quelle", "Inhaltsverzeichnis", "Phasenübersicht"):
            assert text in html
        for text in ("Vertrauliche Maßnahme", "Geheimtext", "Geheimes Elementfoto", "+43664123456", "Nur neuer Arbeitsstand"):
            assert text not in html
        # Der einsatzrelevante Plankontakt darf nur die dienstliche Nummer zeigen.
        assert "+43557112345" in html
        assert "<p>Offener Inhalt</p>" in html
    login(client, inhalte, "readonly")
    response = client.get(basis(inhalte) + "/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "no-store"
    assert '_v2.pdf"' in response.headers["content-disposition"]
    pdf_text = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(response.content)).pages)
    assert "Nur neuer Arbeitsstand" not in pdf_text and "Geheimtext" not in pdf_text
    assert client.get(basis(inhalte) + "/pdf?entwurf=1").status_code == 403
    login(client, inhalte)
    assert client.get(basis(inhalte) + "/pdf?entwurf=1").content.startswith(b"%PDF")
    with TestingSession() as db:
        set_tenant_context(db, None)
        html = render_katplan_html(db, _user(db, inhalte), db.get(KatplanPlan, inhalte["plan_id"]), entwurf=True)
        assert "ENTWURF – nicht freigegeben" in html and "Nur neuer Arbeitsstand" in html
    login(client, inhalte, "fremd")
    assert client.get(basis(inhalte) + "/pdf").status_code == 404


def test_pdf_fremde_version_und_historische_fassung(client, inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        fremd = KatplanPlan(org_id=plan.org_id, titel="Anderer Plan")
        db.add(fremd)
        db.flush()
        lege_element_an(db, fremd, _user(db, inhalte), typ="info", titel="Anderer Inhalt")
        version = freigeben(db, fremd, _user(db, inhalte))
        db.commit()
        vid = version.id
        alt = db.query(KatplanVersion).filter_by(plan_id=plan.id, versionsnummer=1).one()
        alte_id = alt.id
        html = render_katplan_html(db, _user(db, inhalte, "readonly"), plan, version_id=alte_id)
        assert "Alter Inhalt mit 100% und A_B" in html and "Offener Inhalt" not in html
    login(client, inhalte, "readonly")
    assert client.get(basis(inhalte) + f"/pdf?version_id={vid}").status_code == 404
    response = client.get(basis(inhalte) + f"/pdf?version_id={alte_id}")
    assert response.status_code == 200 and '_v1.pdf"' in response.headers["content-disposition"]
    assert "PDF / Offlinefassung" in client.get(basis(inhalte) + "/versionen").text


def test_pdf_fallback(umgebung, monkeypatch):
    import weasyprint

    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = _user(db, umgebung)
        lege_element_an(db, plan, user, typ="info", titel="Fallback")
        freigeben(db, plan, user)
        db.flush()
        def fehler(*args, **kwargs):
            raise OSError("WeasyPrint fehlt")
        monkeypatch.setattr(weasyprint.HTML, "write_pdf", fehler)
        assert render_katplan_pdf(db, user, plan).startswith(b"%PDF")


@pytest.fixture
def quellen(umgebung):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        user = _user(db, umgebung)
        alt = _dokument(db, plan)
        neu = _dokument(db, plan, 2, alt.id)
        fremd = _dokument(db, plan)
        for d, texte in ((alt, {1: "Gleich   bleibt", 2: "Alter Text", 3: "Entfaellt"}),
                         (neu, {1: "Gleich bleibt", 2: "Neuer Text", 4: "Neue Seite"})):
            for nr, text in texte.items():
                db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=d.id,
                                           seiten_nr=nr, volltext=text, einzel_pdf_pfad="seite.pdf"))
        ids = []
        for dokument, seiten in ((alt, "2-3"), (alt, "1"), (neu, "3"), (fremd, "2")):
            e = lege_element_an(db, plan, user, typ="info", titel=f"Element {len(ids)}",
                                quelle_dokument_id=dokument.id, quelle_seiten=seiten)
            ids.append(e.id)
        freigeben(db, plan, user)
        db.commit()
        return {**umgebung, "alt_id": alt.id, "neu_id": neu.id, "fremd_id": fremd.id, "element_ids": ids}


def test_quellenvergleich_und_umstellen(client, quellen):
    info = quellen
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, info["plan_id"])
        result = vergleiche_quellen(db, plan, info["neu_id"])
        assert [s["status"] for s in result["seiten"]] == ["unverändert", "geändert", "entfallen", "neu"]
        assert result["seiten"][0]["quote"] == 1
        assert {e.id for e in result["elemente"]} == {info["element_ids"][0], info["element_ids"][2]}
        assert all(len(s["diff"]) <= 60 for s in result["seiten"])
        assert db.get(KatplanElement, info["element_ids"][0]).quelle_dokument_id == info["alt_id"]
    login(client, info)
    path = basis(info) + f'/quellen/{info["neu_id"]}/vergleich'
    response = client.get(path)
    assert response.status_code == 200
    assert 'class="kp-diff-alt"' in response.text and 'class="kp-diff-neu"' in response.text
    assert "Betroffene Elemente" in response.text and "Quelle auf v2 umstellen" in response.text
    assert "Mit v1 vergleichen" in client.get(basis(info) + "/quellen").text
    eid = info["element_ids"][0]
    ziel = basis(info) + f'/quellen/{info["neu_id"]}/elemente/{eid}/umstellen'
    assert post(client, ziel).status_code == 303
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, eid)
        assert e.quelle_dokument_id == info["neu_id"] and e.quelle_seiten == "2-3"
        assert db.get(KatplanPlan, info["plan_id"]).status == "in_ueberarbeitung"
        a = db.query(KatplanAenderung).filter_by(element_id=eid, aktion="quelle_umgestellt").one()
        assert a.alt == str(info["alt_id"]) and a.neu == str(info["neu_id"]) and a.quelle == "ui"
    assert client.get(path + f'?mit={info["fremd_id"]}').status_code == 404
    assert client.get(basis(info) + f'/quellen/{info["fremd_id"]}/vergleich').status_code == 404
    fremd_ziel = ziel.replace(f'/{info["neu_id"]}/elemente/', f'/{info["fremd_id"]}/elemente/')
    assert post(client, fremd_ziel).status_code == 404
    login(client, info, "readonly")
    assert client.get(path).status_code == 403
    assert post(client, ziel).status_code == 403
    login(client, info, "fremd")
    assert client.get(path).status_code == 404
    assert post(client, ziel).status_code == 404


def test_public_katplan_bullet(client):
    response = client.get("/funktionen")
    assert response.status_code == 200
    assert "Katastrophenpläne mit Freigabe, Karte, Suche und PDF" in response.text
    assert "Anwender-Katastrophenplaene" in response.text



def test_pdf_bildvorschauen_vertraulich_limit_und_sanitizing(inhalte, tmp_path, monkeypatch):
    import base64

    from PIL import Image

    from app.config import settings
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        user = _user(db, inhalte)
        e = db.get(KatplanElement, inhalte["element_id"])
        e.beschreibung_html = '<p>Sicherer Inhalt</p><script>boese()</script><img src="file:///etc/passwd">'
        bilder = []
        for index in range(4):
            buf = io.BytesIO()
            Image.new("RGB", (4, 4), (index * 60, 0, 0)).save(buf, format="JPEG")
            pfad = f"bild{index}.jpg"
            (tmp_path / pfad).write_bytes(buf.getvalue())
            bilder.append(base64.b64encode(buf.getvalue()).decode("ascii"))
            db.add(KatplanAnhang(org_id=plan.org_id, plan_id=plan.id, element_id=e.id,
                                 art="foto", mime="image/jpeg", pfad=pfad, thumb_pfad=pfad,
                                 beschriftung=f"Bild {index}", vertraulich=index == 0, sort=index))
        markiere_ueberarbeitung(db, plan, user)
        freigeben(db, plan, user)
        db.flush()
        for rolle in ("readonly", "katplan_verwalter"):
            html = render_katplan_html(db, _user(db, inhalte, rolle), plan)
            assert html.count('alt="Anhang-Vorschaubild"') == 2
            assert bilder[0] not in html and bilder[3] not in html
            assert bilder[1] in html and bilder[2] in html
            assert "<p>Sicherer Inhalt</p>" in html
            assert "<script>" not in html and "file:///etc/passwd" not in html
        assert "Bild 0" not in render_katplan_html(db, _user(db, inhalte, "readonly"), plan)


def test_pdf_kapitelreihenfolge():
    from app.services.katplan_pdf_service import _kapitelpfade

    abschnitte = [
        {"id": 2, "parent_id": None, "titel": "Problemstellen", "sort": 20},
        {"id": 1, "parent_id": None, "titel": "Maßnahmen", "sort": 10},
        {"id": 3, "parent_id": 1, "titel": "Alarmierung", "sort": 10},
    ]
    assert _kapitelpfade(abschnitte) == ["Maßnahmen", "Maßnahmen > Alarmierung", "Problemstellen"]


def test_pdf_typdaten_lesbar_statt_json():
    from app.services.katplan_pdf_service import _ortsangabe, typdaten_fuer_druck

    bausteine = typdaten_fuer_druck({
        "schritte": [{"text": "Bauhof verständigen", "erledigt_hinweis": ""}, {"text": ""}],
        "bedingungen": [{"wenn": "Pegel steigt", "dann": "Stufe 2"}],
        "bedarf": [{"bezeichnung": "Sandsäcke", "menge": "30", "einheit": "Stk"}],
        "gefahren": ["Verklausung", ""], "kontrollhinweise": "Rechen prüfen", "lagerort": "",
    })
    assert [(b["art"], b["label"]) for b in bausteine] == [
        ("schritte", "Schritte"), ("bedingungen", "Bedingungen"), ("bedarf", "Bedarf"),
        ("liste", "Gefahren"), ("text", "Kontrollhinweise"),
    ]
    assert bausteine[0]["wert"] == [{"text": "Bauhof verständigen", "erledigt_hinweis": ""}]
    assert bausteine[3]["wert"] == ["Verklausung"]
    assert _ortsangabe({"type": "Point", "coordinates": [9.75, 47.47]}, "geprueft") == "Punkt 47.47000, 9.75000 (geprüft)"
    assert _ortsangabe(None, "keine") is None
