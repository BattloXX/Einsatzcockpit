"""Modul-Menüs und Übersicht rendern dieselben Einträge in derselben Reihenfolge."""

import re

import pytest

from app.core.templating import templates
from tests.test_dashboard import _login, _setup_dashboard_user

_HREF = re.compile(r'href="([^"]*)"')
_SONDER_LINKS = {
    "/", "/suche", "/lage", "/wetter", "/archiv", "/admin",
    "https://github.com/BattloXX/Einsatzcockpit/wiki",
}


def _abschnitt(text: str, start: str, ende: str) -> str:
    beginn = text.index(start)
    return text[beginn:text.index(ende, beginn)]


@pytest.mark.parametrize("module_aktiv, ohne_einsatz", [(True, False), (False, False), (True, True)])
def test_jedes_modul_hat_eine_kachel(client, monkeypatch, module_aktiv, ohne_einsatz):
    username = _setup_dashboard_user(modules_enabled=module_aktiv, active_incident=False)
    monkeypatch.setitem(templates.env.globals, "WEATHER_ENABLED", True)
    if ohne_einsatz:
        modul_gruppen = templates.env.globals["modul_gruppen"]
        monkeypatch.setitem(
            templates.env.globals,
            "modul_gruppen",
            lambda request, user: [g for g in modul_gruppen(request, user) if g.key != "einsatz"],
        )
    _login(client, username)

    response = client.get("/")

    assert response.status_code == 200
    desktop = _abschnitt(response.text, 'class="topnav__doku-panel"', "</div>")
    kacheln = _abschnitt(response.text, '<div class="module-grid">', "</section>")
    mobil = _abschnitt(response.text, '<nav class="mobile-menu__nav"', "</nav>")
    modul_hrefs = _HREF.findall(desktop)
    kachel_hrefs = _HREF.findall(kacheln)
    assert modul_hrefs, "Gerendertes Modul-Menü ist leer"
    assert modul_hrefs == [href for href in kachel_hrefs if href not in _SONDER_LINKS]
    assert modul_hrefs == [href for href in _HREF.findall(mobil) if href not in _SONDER_LINKS]
    assert kachel_hrefs[0] == "/lage"
    assert kachel_hrefs[-3:] == [
        "/archiv", "/admin", "https://github.com/BattloXX/Einsatzcockpit/wiki",
    ]
    einsatz = re.search(r'Im Einsatz</span>(.*?)(?:<span class="topnav__doku-grp">|$)', desktop, re.S)
    letzter_einsatz_href = _HREF.findall(einsatz.group(1))[-1] if einsatz else "/lage"
    assert kachel_hrefs[kachel_hrefs.index(letzter_einsatz_href) + 1] == "/wetter"
    if module_aktiv:
        assert '<span class="module-btn__label">Atemschutzprüfung</span>' in kacheln
