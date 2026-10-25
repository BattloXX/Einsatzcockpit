from types import SimpleNamespace

import pytest

from app.core.icons import MODUL_ICONS
from app.services import modul_navigation as navigation

MODUL_FLAGS = (
    "katplan_enabled",
    "nachschlagewerke_enabled",
    "kontakte_enabled",
    "strassensperren_enabled",
    "foerderstrecke_enabled",
    "fahrtenbuch_modul_aktiv",
    "atemschutz_pruefung_modul_aktiv",
    "uas_module_enabled",
    "objekt_enabled",
    "probenplanung_enabled",
    "planspiel_enabled",
    "mailing_module_enabled",
)


def fake_user(*rollen):
    return SimpleNamespace(roles=[SimpleNamespace(code=rolle) for rolle in rollen])


def fake_request(path="/", **flags):
    return SimpleNamespace(state=SimpleNamespace(**flags), url=SimpleNamespace(path=path))


@pytest.fixture(autouse=True)
def berechtigungen(monkeypatch):
    monkeypatch.setattr(
        navigation, "can_view_katplan_request", lambda request: getattr(request.state, "katplan_enabled", False)
    )
    monkeypatch.setattr(navigation, "can_view_kontakt_directory_request", lambda request: True)


@pytest.mark.parametrize("rolle", ["admin", "org_admin", "system_admin"])
def test_admin_alle_module_in_reihenfolge(rolle):
    request = fake_request(**dict.fromkeys(MODUL_FLAGS, True))
    gruppen = navigation.modul_gruppen(request, fake_user(rolle))

    assert [gruppe.key for gruppe in gruppen] == ["einsatz", "begleitend", "verwaltung", "auswertung"]
    assert [gruppe.titel for gruppe in gruppen] == [
        "Im Einsatz", "Einsatzbegleitend", "Verwaltung & Planung", "Auswertung"
    ]
    assert [[eintrag.href for eintrag in gruppe.eintraege] for gruppe in gruppen] == [
        ["/einsatzwissen", "/kontakte", "/strassensperren", "/foerderstrecke/"],
        ["/verwaltung/fahrten", "/atemschutz-pruefung", "/uas/einsaetze"],
        ["/objekte/", "/probenplanung", "/planspiele", "/mailing/"],
        ["/statistik"],
    ]
    assert all(eintrag.icon in MODUL_ICONS for gruppe in gruppen for eintrag in gruppe.eintraege)


@pytest.mark.parametrize("flags", [{}, dict.fromkeys(MODUL_FLAGS, False)])
def test_module_aus_oder_flags_fehlen(flags):
    gruppen = navigation.modul_gruppen(fake_request(**flags), fake_user("mitglied"))

    assert [gruppe.key for gruppe in gruppen] == ["verwaltung", "auswertung"]
    assert [[eintrag.key for eintrag in gruppe.eintraege] for gruppe in gruppen] == [["teilnahme"], ["statistik"]]


def test_probenplanung_ersetzt_teilnahme():
    gruppen = navigation.modul_gruppen(fake_request(probenplanung_enabled=True), fake_user("mitglied"))

    assert [[eintrag.key for eintrag in gruppe.eintraege] for gruppe in gruppen] == [["probenplan"], ["statistik"]]


def test_mailing_und_strassensperren_brauchen_rolle():
    request = fake_request(mailing_module_enabled=True, strassensperren_enabled=True)
    gruppen = navigation.modul_gruppen(request, fake_user("mitglied"))

    assert [eintrag.key for gruppe in gruppen for eintrag in gruppe.eintraege] == ["teilnahme", "statistik"]


@pytest.mark.parametrize("rolle", ["fahrtenbuch_admin", "incident_leader", "recorder"])
@pytest.mark.parametrize("path", ["/fahrtenbuch/xyz", "/verwaltung/fahrten/123"])
def test_fahrtenbuch_aktiv_fuer_berechtigte(rolle, path):
    assert navigation.modul_aktiv(fake_request(path, fahrtenbuch_modul_aktiv=True), fake_user(rolle))


@pytest.mark.parametrize(
    ("path", "aktiviert", "rolle"),
    [("/", True, "admin"), ("/fahrtenbuch/xyz", False, "admin"), ("/fahrtenbuch/xyz", True, "mitglied")],
)
def test_startseite_und_unsichtbare_module_nicht_aktiv(path, aktiviert, rolle):
    assert not navigation.modul_aktiv(fake_request(path, fahrtenbuch_modul_aktiv=aktiviert), fake_user(rolle))


@pytest.mark.parametrize("path, aktiv", [("/statistik/details", True), ("/admin/org/statistik", False)])
def test_statistik_nur_mit_praefix_aktiv(path, aktiv):
    assert navigation.modul_aktiv(fake_request(path), fake_user("mitglied")) is aktiv


def test_request_berechtigungen_werden_beachtet(monkeypatch):
    monkeypatch.setattr(navigation, "can_view_katplan_request", lambda request: False)
    monkeypatch.setattr(navigation, "can_view_kontakt_directory_request", lambda request: False)
    gruppen = navigation.modul_gruppen(fake_request(katplan_enabled=True, kontakte_enabled=True), fake_user("admin"))

    assert [gruppe.key for gruppe in gruppen] == ["verwaltung", "auswertung"]


def test_ohne_user():
    request = fake_request("/statistik", **dict.fromkeys(MODUL_FLAGS, True))

    assert navigation.modul_gruppen(request, None) == []
    assert not navigation.modul_aktiv(request, None)
