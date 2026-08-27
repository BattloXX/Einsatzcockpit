"""Regressionsschutz fuer das eigene Druckregeln-Tab."""
from __future__ import annotations

from app.routers.ui_gateway import _rule_return
from app.models.master import OrgSettings
from tests.test_gateway_routes import _match_endpoint


def test_rule_return_verweist_auf_druckregeln_tab():
    assert _rule_return(7, "rule_saved=1#regel-3") == (
        "/gateway/7/druckregeln?rule_saved=1#regel-3"
    )
    assert _rule_return(None, "rule_deleted=1#regeln") == "/gateway?rule_deleted=1#regeln"


def test_org_settings_hat_keinen_alten_verleih_autodruck_mehr():
    assert not hasattr(OrgSettings, "verleih_autodruck")


def test_alte_verleih_autodruck_route_existiert_nicht():
    assert _match_endpoint("/gateway/1/verleih-autodruck", "POST") is None
