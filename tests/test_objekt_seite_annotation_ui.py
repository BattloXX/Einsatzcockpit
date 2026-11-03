"""P3: Einzeichnungen im Objekt-Viewer, in der Galerie und im Editor."""
import io
import json
import re
from html import unescape

import pytest
from PIL import Image

from app.services import annotation_service as ann
from app.services.objekt_seite_annotation import annotierte_seiten
from tests import test_objekt_seite_annotation as annotation_tests
from tests.test_objekt_dokumente_wartend_ui import _login
from tests.test_objekt_seite_annotation import _save

annotation_env = annotation_tests.env


@pytest.fixture
def env(annotation_env):
    annotation_env.dokument.ist_aktuelle_version = True
    annotation_env.db.commit()
    return annotation_env


@pytest.mark.parametrize("rolle", ["objekt_verwalter", "readonly"])
def test_viewer_schreibaktionen_nur_mit_recht(client, env, rolle):
    _login(client, env.users[rolle].username)
    response = client.get(f"/objekte/{env.objekt.id}/dokumente/viewer?seite={env.seite.id}")
    assert response.status_code == 200
    assert ('Einzeichnen</a>' in response.text) == (rolle == "objekt_verwalter")
    assert ('Einzeichnung entfernen</button>' in response.text) == (rolle == "objekt_verwalter")
    assert 'dreheLinks()' in response.text
    assert 'tempRot' in response.text


def test_viewer_bearbeitet_cache_und_original(client, env):
    _save(env)
    env.db.commit()
    timestamp = annotierte_seiten(env.db, [env.seite.id])[env.seite.id]
    _login(client, env.users["readonly"].username)
    response = client.get(f"/objekte/{env.objekt.id}/dokumente/viewer?seite={env.seite.id}")
    assert response.status_code == 200
    state = re.search(r"bearbeitet: (\{.*?\}),", response.text)
    assert json.loads(state[1]) == {str(env.seite.id): timestamp}
    assert '>Bearbeitet</span>' in response.text
    assert 'Original anzeigen' in response.text
    assert "'&v='" in response.text
    assert "'&original=1'" in response.text
    assert "seitenUrl(seiten[idx], 'pdf')" in unescape(response.text)
    assert "seitenUrl(sid, 'bild')" in unescape(response.text)


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_editor_objektseite_konfiguration_und_gedrehtes_original(client, env, rotation):
    env.seite.rotation = rotation
    _save(env)
    env.db.commit()
    _login(client, env.users["objekt_verwalter"].username)
    response = client.get(f"/annotieren/objekt_seite/{env.seite.id}")
    assert response.status_code == 200
    assert '<title>Dokumentseite bearbeiten' in response.text
    assert 'plan.pdf - Seite 1' in response.text
    assert 'overlay: true' in response.text
    assert f'backUrl: "/objekte/{env.objekt.id}/dokumente/viewer?seite={env.seite.id}"' in response.text
    assert 'document.referrer' not in response.text
    config = re.search(r"annotationJson: (.*),", response.text)
    assert json.loads(config[1]) == ann.get_annotation(env.db, "objekt_seite", env.seite.id).annotation_json
    original = client.get(f"/annotieren/objekt_seite/{env.seite.id}/original")
    assert original.status_code == 200
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.size == ((120, 80) if rotation else (80, 120))


@pytest.mark.parametrize("rolle", ["objekt_verwalter", "readonly"])
def test_galerie_badge_cache_drehsperre(client, env, rolle):
    _save(env)
    env.db.commit()
    timestamp = annotierte_seiten(env.db, [env.seite.id])[env.seite.id]
    _login(client, env.users[rolle].username)
    response = client.get(f"/objekte/{env.objekt.id}/dokumente")
    assert response.status_code == 200
    assert 'aria-label="Bearbeitet" title="Bearbeitet"' in response.text
    assert f'/seite/{env.seite.id}/thumb?v={timestamp}' in response.text
    assert f'/seite/{env.seiten[1].id}/thumb?r=0' in response.text
    assert (f'href="/annotieren/objekt_seite/{env.seite.id}"' in response.text) == (rolle == "objekt_verwalter")
    assert ('disabled title="Seite enthält Einzeichnungen"' in response.text) == (rolle == "objekt_verwalter")


def test_drehen_konflikt_als_htmx_toast_detail(client, env):
    _save(env)
    env.db.commit()
    _login(client, env.users["objekt_verwalter"].username)
    response = client.post(
        f"/objekte/{env.objekt.id}/dokumente/seite/{env.seite.id}/drehen",
        data={"richtung": "rechts", "_csrf": client.cookies.get("ec_csrf")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Seite enthält Einzeichnungen")
