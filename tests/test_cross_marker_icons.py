"""Cross-Marker liefern Tabler-Namen und behalten Fachzeichen sowie Emoji-Fallbacks."""

import json
import re

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import CROSS_MARKER_TYPE_ICON, CROSS_MARKER_TYPE_ICON_NAME, CrossSiteMarker
from app.routers.ui_major_incident import _cross_to_json
from tests.test_gsl_sektoren import _login, _user_lage


def test_cross_marker_icons_in_json_board_panel_and_map(client, setup_db):
    lage_id, org_id = _user_lage("cross_icons", "cross-icons")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        markers = [
            CrossSiteMarker(
                major_incident_id=lage_id, org_id=org_id, title='Test <Marker> "&',
                marker_type=kind, status="unbestaetigt", lat=47.5, lng=9.7,
            )
            for kind in CROSS_MARKER_TYPE_ICON
        ]
        db.add_all(markers)
        db.commit()
        ids = {m.marker_type: m.id for m in markers}
        for m in markers:
            data = _cross_to_json(m)
            assert data["icon"] == CROSS_MARKER_TYPE_ICON[m.marker_type]
            assert data["icon_name"] == CROSS_MARKER_TYPE_ICON_NAME.get(m.marker_type)
    finally:
        db.close()

    _login(client, "cross_icons")
    response = client.get(f"/lage/{lage_id}/karte-cross-markers")
    assert response.status_code == 200
    live = response.json()
    assert len(live) == len(ids)
    for data in live:
        kind = data["marker_type"]
        assert data["type_icon"] == CROSS_MARKER_TYPE_ICON[kind]
        assert data["type_icon_name"] == CROSS_MARKER_TYPE_ICON_NAME.get(kind)
        assert data["status_color"] == "#6b7280"

    response = client.get(f"/lage/{lage_id}/karte")
    assert response.status_code == 200
    initial = json.loads(re.search(r"var CROSS_MARKERS\s*= (.*);", response.text)[1])
    assert initial == live
    board = client.get(f"/lage/{lage_id}/uebergreifend/board-col")
    assert board.status_code == 200
    assert 'ecp-fw-zeichen' in board.text
    assert 'Test &lt;Marker&gt; &#34;&amp;' in board.text
    for kind in ("unterfuehrung_geflutet", "mure"):
        panel = client.get(f"/lage/{lage_id}/uebergreifend/{ids[kind]}/panel")
        assert panel.status_code == 200
        assert 'aria-label="Bearbeiten"' in panel.text
        assert 'Test &lt;Marker&gt; &#34;&amp;' in panel.text
        if kind == "mure":
            assert '<span class="ecp-fw-zeichen" aria-hidden="true">🪨</span>' in panel.text
        else:
            assert CROSS_MARKER_TYPE_ICON[kind] not in panel.text
