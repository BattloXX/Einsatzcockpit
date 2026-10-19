"""Der öffentliche CSP-Endpunkt liefert unabhängig vom Tenant keine Daten."""

from app.db import SessionLocal
from app.models.master import FireDept


def test_csp_report_liefert_keine_org_daten(client):
    """Auch mit fremden Org-IDs bleibt der anonyme Meldeendpunkt vollständig leer."""
    with SessionLocal() as db:
        org_b = FireDept(slug="csp-isolation-b", name="Geheime Organisation B")
        db.add(org_b)
        db.commit()
        org_b_id = org_b.id
    client.cookies.clear()
    for org_id in (1, org_b_id):
        response = client.post(
            f"/csp-report?org_id={org_id}",
            json={"org_id": org_id, "csp-report": {
                "effective-directive": "script-src",
                "blocked-uri": "inline",
                "document-uri": f"https://example.test/org/{org_id}/daten",
            }},
        )
        assert response.status_code == 204
        assert response.content == b""
