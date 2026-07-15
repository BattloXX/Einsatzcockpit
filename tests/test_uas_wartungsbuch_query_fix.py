"""Regression: `_render_uas` (print_artifact_service.py) crashte bei jedem
"wartungsbuch"-PDF-Druck mit AttributeError, weil Query und Rendering auf Feldnamen
zugriffen, die `UASWartung` nie hatte (uas_device_id/faellig_am/typ/durchgefuehrt_am/
techniker statt device_id/datum/art/naechste_faellig/pruefer, siehe app/models/uas.py).
Der mypy-Cleanup deckte das bei der Query auf (attr-defined); dieser Test prueft den
kompletten Pfad end-to-end gegen eine echte DB-Zeile (WeasyPrint gemockt wie in
test_uas_pr7.py)."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.uas import UASDevice, UASWartung

ORG_ID = 1  # FF Wolfurt (seeded)


def _fake_render(html: str) -> bytes:
    return html.encode()


def test_render_uas_wartungsbuch_ohne_attribute_error():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        device = UASDevice(org_id=ORG_ID, bezeichnung="DJI Air 3 Test-WB")
        db.add(device)
        db.flush()
        db.add(UASWartung(
            org_id=ORG_ID, device_id=device.id, datum=date(2026, 6, 30),
            art="monatliche_sichtkontrolle", ergebnis="io", pruefer="H. Muster",
            naechste_faellig=date(2026, 7, 30),
        ))
        db.commit()
        device_id = device.id
    finally:
        db.close()

    from app.services.print_artifact_service import _render_uas

    db2 = SessionLocal()
    set_tenant_context(db2, None)
    try:
        job = SimpleNamespace(artifact_ref=f"wartungsbuch:{device_id}", org_id=ORG_ID)
        with patch("app.services.uas_pdf._render_pdf", side_effect=_fake_render):
            result = _render_uas(db2, job)
        html = result.decode()
        assert "DJI Air 3 Test-WB" in html
        assert "H. Muster" in html
        assert "monatliche sichtkontrolle" in html
        assert "2026-07-30" in html  # naechste_faellig
        assert "2026-06-30" in html  # datum
    finally:
        db2.close()
