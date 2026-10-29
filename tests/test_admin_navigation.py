"""Katalog, Sichtbarkeit und Rendering der unveraenderten Admin-Navigation."""

from types import SimpleNamespace

import pytest

from app.core.templating import templates
from app.services import admin_navigation as navigation
from tests.test_systemstatus_nav import _login, _make_org_admin

FLAGS = (
    "foerderstrecke_enabled", "probenplanung_enabled", "gateway_enabled",
    "strassensperren_enabled", "planspiel_enabled",
)


def _request(path="/admin", **flags):
    return SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace(**flags))


def _user(*roles):
    return SimpleNamespace(roles=[SimpleNamespace(code=role) for role in roles])


def _nav(path="/admin", role="admin", enabled=True):
    return navigation.admin_navigation(_request(path, **dict.fromkeys(FLAGS, enabled)), _user(role))


def _entries(nav):
    return [entry for group in nav.gruppen for entry in group.eintraege]


def test_katalog_konsistent():
    ids = []
    for group in navigation.ADMIN_GRUPPEN:
        for entry in group.eintraege:
            ids.append(entry.id)
            assert entry.gruppe == group.key
            assert entry.href.startswith("/")
            assert len({tab.id for tab in entry.tabs}) == len(entry.tabs)
            assert all(tab.href.startswith("/") for tab in entry.tabs)
    assert len(set(ids)) == len(ids)
    assert len({group.key for group in navigation.ADMIN_GRUPPEN}) == len(navigation.ADMIN_GRUPPEN)


@pytest.mark.parametrize("role", ["admin", "org_admin", "system_admin"])
def test_vollrechte_und_medien_genau_einmal(role):
    nav = _nav(role=role)
    assert [group.key for group in nav.gruppen] == ["stammdaten", "verwaltung", "einstellungen"]
    entries = _entries(nav)
    media = [entry for entry in entries if entry.href == "/admin/medien"]
    assert len(media) == 1
    assert media[0].gruppe == ("einstellungen" if role == "system_admin" else "verwaltung")
    assert (nav.eintrag("systemdiagnose") is not None) == (role == "system_admin")
    assert (nav.eintrag("medien_system") is not None) == (role == "system_admin")
    assert all(group.ueberschrift for group in nav.gruppen)


@pytest.mark.parametrize("role", ["mitglied", "uebungsleiter", "mitglieder_admin"])
def test_eingeschraenkt_nur_mitglieder_qualifikationen(role):
    nav = _nav(role=role)
    assert [entry.id for entry in _entries(nav)] == ["mitglieder", "qualifikationen"]
    assert all(not group.ueberschrift for group in nav.gruppen)


def test_ohne_user_keine_navigation():
    assert not navigation.admin_navigation(_request(), None).gruppen


@pytest.mark.parametrize("role", ["admin", "org_admin", "system_admin"])
@pytest.mark.parametrize("flag", FLAGS)
def test_modulflags_einzeln(role, flag):
    off = navigation.admin_navigation(_request(**dict.fromkeys(FLAGS, False)), _user(role))
    on = navigation.admin_navigation(_request(**{flag: True}), _user(role))
    expected = {entry.id for group in navigation.ADMIN_GRUPPEN for entry in group.eintraege
                if entry.modulflag == flag}
    assert expected
    assert {e.id for e in _entries(on)} - {e.id for e in _entries(off)} == expected
    tabs = on.eintrag("infoscreens").tabs
    assert any(t.id == "strassensperren_infoscreen" for t in tabs) == (flag == "strassensperren_enabled")


@pytest.mark.parametrize("role", ["admin", "system_admin"])
@pytest.mark.parametrize("path,entry_id", [
    ("/admin/benutzer/1", "benutzer"),
    ("/admin/mail", "mail"), ("/admin/mail/eingang/1", "mail_eingang"),
    ("/admin/settings", "settings"), ("/admin/settings/wetter", "settings_wetter"),
    ("/admin/settings/wetter/infoscreen", "infoscreens"),
    ("/admin/settings/statistik", "infoscreens"),
    ("/admin/sms-senden", "sms"), ("/admin/sms-provider", "sms"),
    ("/admin/sms-empfang", "sms"), ("/admin/einsatzinfo-sms", "sms"),
    ("/admin/geraete-login/1", "zugaenge"), ("/admin/api-keys", "zugaenge"),
    ("/admin/lagekarte-tokens", "zugaenge"),
    ("/admin/auftragsvorlagen", "einsatzvorlagen"),
    ("/admin/meldungsvorlagen", "einsatzvorlagen"),
    ("/admin/lage-hinweise", "einsatzvorlagen"),
    ("/admin/default-meldungen", "einsatzvorlagen"),
    ("/admin/foerderpumpen", "foerderstrecke"),
    ("/admin/foerderschlaeuche", "foerderstrecke"),
    ("/admin/foerderkalibrierung", "foerderstrecke"),
    ("/infoscreen-alarm/verwaltung", "infoscreens"),
    ("/infoscreen-alarm/1", "infoscreens"),
    ("/gateway/drucker", "gateway"),
    ("/admin/settings/planspiele", "settings_planspiele"),
    ("/admin/settings/planspiele/szenarien/1", "settings_planspiele_szenarien"),
    ("/admin/settings/strassensperren-infoscreen", "settings_strassensperren_infoscreen"),
    ("/strassensperren/infoscreen", None),
])
def test_eindeutige_aktivitaet(role, path, entry_id):
    nav = _nav(path, role)
    matches = [e for e in _entries(nav) if e.ist_aktiv(path)]
    assert [e.id for e in matches] == ([entry_id] if entry_id else [])
    assert (nav.aktiver_eintrag.id if nav.aktiver_eintrag else None) == entry_id
    if matches and matches[0].tabs:
        assert len([t for t in matches[0].tabs if t.ist_aktiv(path)]) == 1
        assert nav.aktiver_tab is not None


@pytest.mark.parametrize("role", ["admin", "system_admin"])
def test_medien_aktiv_und_mail_weiterleitungen(role):
    nav = _nav("/admin/medien", role)
    assert nav.aktiver_eintrag.href == "/admin/medien"
    assert nav.aktive_gruppe == ("einstellungen" if role == "system_admin" else "verwaltung")
    nav = _nav("/admin/mail-weiterleitungen", role)
    assert len([e for e in _entries(nav) if e.ist_aktiv("/admin/mail-weiterleitungen")]) == 1
    assert nav.aktiver_eintrag.id == ("mail_weiterleitungen" if role == "system_admin" else "mail")


def test_tabs_rollen_flag_filter(monkeypatch):
    entry = navigation.AdminEintrag(
        "test", "Test", "test", "settings", "/test", ("/test",), tabs=(
            navigation.AdminTab("admin", "Admin", "/test", ("/test",), rollen=("system_admin",)),
            navigation.AdminTab("flag", "Flag", "/test/flag", ("/test/flag",), modulflag="gateway_enabled"),
        ),
    )
    monkeypatch.setattr(navigation, "ADMIN_GRUPPEN", (navigation.AdminGruppe("test", "Test", (entry,)),))
    assert [t.id for t in _nav().eintrag("test").tabs] == ["flag"]
    assert not _nav(enabled=False).eintrag("test").tabs
    assert len(_nav(role="system_admin").eintrag("test").tabs) == 2


def test_section_tabs_und_suche():
    request = _request("/admin/settings/strassensperren-infoscreen", **dict.fromkeys(FLAGS, True))
    rendered = templates.env.get_template("admin/_section_tabs.html").render(
        request=request, user=_user("admin"), section_id="infoscreens",
    )
    assert rendered.count('aria-selected="true"') == 1
    assert 'href="/admin/settings/strassensperren-infoscreen"' in rendered
    request.state.strassensperren_enabled = False
    rendered = templates.env.get_template("admin/_section_tabs.html").render(
        request=request, user=_user("admin"), section_id="infoscreens",
    )
    assert "Straßensperren" not in rendered


@pytest.mark.parametrize("path", ["/admin", "/admin/benutzer", "/admin/sms-senden", "/infoscreen-alarm/verwaltung"])
def test_rendering_smoke(client, setup_db, path):
    suffix = path.strip("/").replace("/", "-")
    _make_org_admin(f"navigation-{suffix}", f"navigation-{suffix}")
    _login(client, f"navigation-{suffix}", "Test1234!")
    response = client.get(path)
    assert response.status_code == 200
    assert 'href="/admin/benutzer"' in response.text
    assert 'href="/admin/systemstatus"' in response.text
    assert 'href="/admin/sms-senden"' in response.text
    assert 'data-nav-search="SMS SMS senden SMS-Versandweg SMS-Empfang SMS-Einsatzinfo"' in response.text


@pytest.mark.parametrize("role", ["admin", "org_admin", "system_admin"])
def test_alle_katalog_ziele_eindeutig(role):
    for entry in _entries(_nav(role=role)):
        nav = _nav(entry.href, role)
        assert len([e for e in _entries(nav) if e.ist_aktiv(entry.href)]) == (0 if entry.neues_fenster else 1)
        for tab in entry.tabs:
            nav = _nav(tab.href, role)
            assert nav.aktiver_tab.id == tab.id
            assert sum(t.ist_aktiv(tab.href) for t in entry.tabs) == 1


@pytest.mark.parametrize("path,group", [
    ("/admin/settings/unbekannt", "einstellungen"),
    ("/admin/probenplanung/unbekannt", "verwaltung"),
    ("/admin/settings/wetter/infoscreen", "verwaltung"),
    ("/admin/foerderpumpen", "verwaltung"),
    ("/admin/einsatzinfo-mail", "verwaltung"),
    ("/admin/wordpress-berichte", "stammdaten"),
    ("/admin/org-backup", "stammdaten"),
    ("/admin/hilfe", "hilfe"),
])
def test_bisherige_gruppenoeffnung_auch_ohne_modulflag(path, group):
    assert _nav(path, enabled=False).aktive_gruppe == group
