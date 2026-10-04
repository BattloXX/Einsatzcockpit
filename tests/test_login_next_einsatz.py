"""Rücksprung nach dem Login: Deep-Links auf Einsatz-Seiten behalten ihr Ziel."""
from urllib.parse import quote

HTML = {"accept": "text/html"}


def test_einsatzinfo_anonym_leitet_mit_next_zum_login(client):
    r = client.get("/einsatz/1/info", headers=HTML, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/login?next={quote('/einsatz/1/info', safe='')}"


def test_einsatzboard_anonym_leitet_mit_next_zum_login(client):
    r = client.get("/einsatz/1", headers=HTML, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/login?next={quote('/einsatz/1', safe='')}"


def test_next_behaelt_die_query_des_deep_links(client):
    r = client.get("/einsatz/1/info?quelle=visu", headers=HTML, follow_redirects=False)
    assert r.headers["location"] == f"/login?next={quote('/einsatz/1/info?quelle=visu', safe='')}"


def test_login_seite_reicht_next_an_die_sso_weiterleitung(client):
    r = client.get("/login?next=/einsatz/7/info", headers=HTML)
    assert r.status_code == 200
    assert 'var ssoNext = "/einsatz/7/info";' in r.text
    assert "ssoNextQs" in r.text
    assert 'name="next" value="/einsatz/7/info"' in r.text


def test_login_seite_ohne_next_hat_leeres_sso_ziel(client):
    r = client.get("/login", headers=HTML)
    assert 'var ssoNext = "";' in r.text


def test_next_kann_kein_script_ausbrechen(client):
    r = client.get("/login?next=/x</script><script>alert(1)</script>", headers=HTML)
    assert r.status_code == 200
    assert "</script><script>alert(1)" not in r.text
