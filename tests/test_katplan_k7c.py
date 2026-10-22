"""K7c: Quellenoberflaeche, Seitenuebernahme und historische Referenzen."""

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAbschnitt, KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan,
)
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte
from tests.test_katplan_k6b import Bereiche, freigeben
from tests.test_katplan_k7b import storage, upload  # noqa: F401


def hochladen(client, info, **werte):
    response = upload(client, info, **werte)
    assert response.status_code == 200, response.text
    return objekte(info, KatplanDokument)[-1]


def quelle_path(info, dokument):
    return basis(info) + f"/quellen/{dokument.id}"


def test_quellen_reiter_upload_und_leser(client, umgebung):
    login(client, umgebung)
    alt = hochladen(client, umgebung, herausgeber="Gemeinde", stand="2026-10-19")
    anlegen(client, umgebung, typ="info")
    freigeben(client, umgebung)
    login(client, umgebung)
    neu = hochladen(client, umgebung, titel="Neue Arbeitsquelle", ersetzt_dokument_id=alt.id)
    page = client.get(basis(umgebung) + "/quellen")
    assert page.status_code == 200
    assert 'accept="application/pdf"' in page.text and 'hx-encoding="multipart/form-data"' in page.text
    assert "Gemeinde" in page.text and "19.10.2026" in page.text and "v2 (ersetzt v1)" in page.text
    assert "fertig" in page.text and "hx-confirm" in page.text
    parser = Bereiche()
    parser.feed(page.text)
    assert not parser.form
    assert len(parser.ids) == len(set(parser.ids))
    for suffix in ("?ansicht=arbeitsstand", "/versionen", "/karte", "#kp-protokoll"):
        assert f'href="{basis(umgebung)}/quellen"' in client.get(basis(umgebung) + suffix).text
    login(client, umgebung, "readonly")
    page = client.get(basis(umgebung) + "/quellen")
    assert page.status_code == 200 and "Planquelle" in page.text and "Neue Arbeitsquelle" not in page.text
    assert 'name="datei"' not in page.text and 'name="ersetzt_dokument_id"' not in page.text
    assert client.get(quelle_path(umgebung, neu)).status_code == 404
    assert client.get(quelle_path(umgebung, alt)).status_code == 200
    login(client, umgebung, "fremd")
    assert client.get(basis(umgebung) + "/quellen").status_code == 404


def test_seitenraster_einzelseite_fallback_und_tenant(client, umgebung):
    login(client, umgebung)
    dokument = hochladen(client, umgebung)
    path = quelle_path(umgebung, dokument)
    raster = client.get(path)
    assert raster.status_code == 200
    assert 'class="kp-seitenraster"' in raster.text and 'loading="lazy"' in raster.text
    assert '?seite=1' in raster.text and '?seite=2' in raster.text
    parser = Bereiche()
    parser.feed(client.get(path + "?seite=1").text)
    for art, kind in (("bild", "image"), ("pdf", "pdf")):
        link = next(a for a in parser.links if a.get("href") == f"/plaene/quelle/{dokument.uuid}/seite/1/{art}")
        assert f"openMediaViewer(this.href, '{kind}'," in link["onclick"]
        assert "target" not in link
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        seite = db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id, seiten_nr=1).one()
        seite.volltext = "Seitentitel\nAusführlicher Volltext"
        seite.bild_pfad = None
        db.commit()
    page = client.get(path + "?seite=1")
    assert page.status_code == 200
    assert "Volltext" in page.text and "Ausführlicher Volltext" in page.text
    assert "Seite als PDF öffnen" in page.text and 'class="kp-seitenbild"' not in page.text
    assert "Download Original" in page.text and "Nächste Seite" in page.text
    assert 'name="seite"' in page.text and 'value="Seitentitel"' in page.text
    assert "Vorherige Seite" in client.get(path + "?seite=2").text
    for nr in (0, -1, 3):
        assert client.get(path + f"?seite={nr}").status_code == 404
    login(client, umgebung, "fremd")
    assert client.get(path).status_code == client.get(path + "?seite=1").status_code == 404
    assert post(client, path + "/element", seite=1, typ="info", titel="Fremd").status_code == 404


def test_element_aus_seite_editor_und_verknuepfung(client, umgebung):
    login(client, umgebung)
    dokument = hochladen(client, umgebung)
    path = quelle_path(umgebung, dokument)
    post(client, basis(umgebung) + "/abschnitte", titel="Kapitel")
    kapitel = objekte(umgebung, KatplanAbschnitt)[0]
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        seite = db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id, seiten_nr=1).one()
        seite.titel = "Übernommener Titel"
        seite.volltext = 'Erster Absatz\n\n<script>alert(1)</script>\n<img src=x onerror="boom()">'
        db.commit()
    assert 'value="Übernommener Titel"' in client.get(path + "?seite=1").text
    response = client.post(path + "/element", data={
        "_csrf": client.cookies.get("ec_csrf"), "seite": 1, "typ": "info",
        "titel": "Importierte Information", "abschnitt_id": kapitel.id,
    }, follow_redirects=False)
    assert response.status_code == 303
    element = objekte(umgebung, KatplanElement)[0]
    assert (element.quelle_dokument_id, element.quelle_seiten, element.herkunft) == (dokument.id, "1", "imported")
    assert element.abschnitt_id == kapitel.id
    assert "<p>Erster Absatz</p>" in element.beschreibung_html
    assert "<script" not in element.beschreibung_html and "<img" not in element.beschreibung_html
    assert "&lt;script&gt;" in element.beschreibung_html
    assert response.headers["location"] == basis(umgebung) + f"/bearbeiten?element={element.id}"
    editor = client.get(response.headers["location"])
    assert editor.status_code == 200
    assert f'id="kp-element-version" value="{element.version}"' in editor.text
    assert f'/elemente/{element.id}"' in editor.text and 'class="kp-aktiv"' in editor.text
    assert "Importierte Information" in client.get(path + "?seite=1").text
    assert client.get(basis(umgebung) + "/bearbeiten?element=99999999").status_code == 404
    invalid = post(client, path + "/element", seite=1, typ="ungueltig", titel="Fehler")
    assert invalid.status_code == 422 and len(objekte(umgebung, KatplanElement)) == 1
    assert post(client, path + "/element", seite=99, typ="info", titel="Fehler").status_code == 404
    response = post(client, path + "/element", seite=2, typ="info", titel="HTMX-Import")
    assert response.status_code == 200 and "?element=" in response.headers["HX-Redirect"]
    login(client, umgebung, "readonly")
    assert post(client, path + "/element", seite=1, typ="info", titel="Verboten").status_code == 403


def test_quellenformular_herkunft_und_versionstitel(client, umgebung, monkeypatch):
    login(client, umgebung)
    dokument = hochladen(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    path = basis(umgebung) + f"/elemente/{element.id}"
    assert 'name="quelle_dokument_id"' in client.get(path).text
    assert post(client, path, **element_form(element, quelle_dokument_id=dokument.id, quelle_seiten="1-2")).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanElement, element.id).herkunft = "imported"
        db.commit()
    html = client.get(basis(umgebung) + "?ansicht=arbeitsstand").text
    assert "Planquelle (v1), S. 1-2" in html and "aus Dokument übernommen" in html
    assert f'/quellen/{dokument.id}?seite=1' in html
    version_id = freigeben(client, umgebung)
    login(client, umgebung)
    neu = hochladen(client, umgebung, titel="Neue Quelle", ersetzt_dokument_id=dokument.id)
    form = client.get(path).text
    assert "Planquelle (v1) (ältere Version)" in form and "Neue Quelle (v2)" in form
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanDokument, dokument.id).titel = "Nachträglich geändert"
        db.get(KatplanElement, element.id).herkunft = "ai_suggested"
        db.commit()
    assert "KI-Vorschlag" in client.get(basis(umgebung) + "?ansicht=arbeitsstand").text
    login(client, umgebung, "readonly")
    html = client.get(basis(umgebung) + f"/versionen/{version_id}").text
    assert "Planquelle (v1), S. 1-2" in html and "Nachträglich geändert" not in html
    assert f'version_id={version_id}' in html and "aus Dokument übernommen" in html
    parser = Bereiche()
    parser.feed(html)
    for art, kind in (("bild", "image"), ("pdf", "pdf")):
        link = next(a for a in parser.links if a.get("href") ==
                    f"/plaene/quelle/{dokument.uuid}/seite/1/{art}?version_id={version_id}")
        assert f"openMediaViewer(this.href, '{kind}'," in link["onclick"]
    assert client.get(quelle_path(umgebung, dokument) + f"?seite=1&version_id={version_id}").status_code == 200
    assert client.get(quelle_path(umgebung, neu) + f"?version_id={version_id}").status_code == 404
    from app.routers import ui_katplan

    lade_version = ui_katplan._lade_version

    def ohne_quellen(*args, **kwargs):
        version, daten, elemente = lade_version(*args, **kwargs)
        return version, {**daten, "quellen": []}, elemente

    monkeypatch.setattr(ui_katplan, "_lade_version", ohne_quellen)
    assert "Quelle nicht mehr verfügbar" in client.get(basis(umgebung)).text


@pytest.mark.parametrize("status,pollt", [("neu", True), ("verarbeitung", True), ("fertig", False), ("fehler", False)])
def test_status_polling(client, umgebung, status, pollt):
    login(client, umgebung)
    dokument = hochladen(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        d = db.get(KatplanDokument, dokument.id)
        d.status, d.fehler_text = status, "Lesefehler" if status == "fehler" else None
        db.commit()
    response = client.get(quelle_path(umgebung, dokument) + "/status")
    assert response.status_code == 200 and status in response.text
    assert ('hx-trigger="every 2s"' in response.text) is pollt
    if status == "fehler":
        assert "Lesefehler" in response.text and "kp-badge--red" in response.text


def test_uploadfehler_und_archivhinweis(client, umgebung, monkeypatch):
    from fastapi import HTTPException
    from app.services import katplan_quelle_service

    login(client, umgebung)
    dokument = hochladen(client, umgebung)
    missing = post(client, basis(umgebung) + "/quellen", titel="Fehlende Datei")
    assert missing.status_code == 422 and 'role="alert"' in missing.text
    assert upload(client, umgebung, titel="").status_code == 422
    anlegen(client, umgebung, typ="info")
    freigeben(client, umgebung)
    login(client, umgebung)
    response = post(client, quelle_path(umgebung, dokument) + "/loeschen")
    assert response.status_code == 200 and "archiviert" in response.text
    for status in (413, 415, 422):
        def fehler(*args, **kwargs):
            raise HTTPException(status, "Uploadfehler")

        monkeypatch.setattr(katplan_quelle_service, "quelle_hochladen", fehler)
        response = upload(client, umgebung)
        assert response.status_code == status
        assert 'role="alert"' in response.text and 'id="kp-quellen"' in response.text
        assert "Uploadfehler" in response.text
    login(client, umgebung, "readonly")
    assert client.get(basis(umgebung) + "/quellen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanPlan, umgebung["plan_id"]).status = "archiviert"
        db.commit()
    assert client.get(basis(umgebung) + "/quellen").status_code == 404


def test_seitentext_aufzaehlung_und_titel_kuerzen():
    from types import SimpleNamespace

    from app.routers.ui_katplan import _seitentext_als_html, _seitentitel

    text = "1. Maßnahmen bei hohem Abfluss • Kontrollfahrten • Bauhof <b>verständigen</b>\nSchluss"
    assert _seitentext_als_html(text) == (
        "<p>1. Maßnahmen bei hohem Abfluss</p><ul><li>Kontrollfahrten</li>"
        "<li>Bauhof &lt;b&gt;verständigen&lt;/b&gt;</li></ul><p>Schluss</p>"
    )
    assert _seitentitel(SimpleNamespace(titel=None, volltext=text), "Fallback") == "1. Maßnahmen bei hohem Abfluss"
    lang = SimpleNamespace(titel=None, volltext="Wort " * 60)
    titel = _seitentitel(lang, "Fallback")
    assert len(titel) <= 124 and titel.endswith(" ...")
    assert _seitentitel(SimpleNamespace(titel=None, volltext=""), "Fallback") == "Fallback"
