"""Verwaltungs-Startseite: Wiki-Verweise zeigen auf vorhandene Seiten, Icons existieren."""
from pathlib import Path

from app.services.admin_startseite import EINRICHTUNG, WIKI_BEREICHE, einrichtung_fuer, wiki_bereiche_fuer

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "docs" / "wiki"


def _slugs():
    yield from (s.wiki for s in EINRICHTUNG)
    for bereich in WIKI_BEREICHE:
        yield from (link.slug for link in bereich.links)


def test_alle_wiki_slugs_existieren():
    fehlend = sorted({slug for slug in _slugs() if not (WIKI / f"{slug}.md").exists()})
    assert not fehlend, fehlend


def test_alle_icons_der_startseite_sind_vendort():
    erlaubt = set((ROOT / "app/static/icons/tabler-auswahl.txt").read_text(encoding="utf-8").split())
    namen = {b.icon for b in WIKI_BEREICHE} | {link.icon for b in WIKI_BEREICHE for link in b.links}
    assert not sorted(namen - erlaubt), sorted(namen - erlaubt)


def test_sysadmin_sichtbarkeit():
    assert len(einrichtung_fuer(False)) < len(einrichtung_fuer(True))
    assert all(b.sichtbarkeit == "alle" for b in wiki_bereiche_fuer(False))
    assert any(b.sichtbarkeit == "sysadmin" for b in wiki_bereiche_fuer(True))


def test_startseite_zeigt_hauptpunkte_einrichtung_und_wiki(client, setup_db):
    from tests.test_systemstatus_nav import _login, _make_org_admin

    _make_org_admin("startseite-orgadmin", "startseite-orgadmin")
    _login(client, "startseite-orgadmin", "Test1234!")
    response = client.get("/admin")
    assert response.status_code == 200
    text = response.text
    assert "Verwaltung auf einen Blick" in text
    assert "Einrichtung in sinnvoller Reihenfolge" in text
    assert "wiki/Administration-Benutzer-und-Rollen" in text
    assert 'href="/admin/benutzer"' in text
    # Systemadmin-Inhalte nur fuer Systemadmins
    assert "Sicherheit und Betrieb" not in text
    assert "Absichern" not in text
