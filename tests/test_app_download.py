"""Öffentliche Installationsseite und Android-Download."""
import httpx

from app.services import app_download_service


def _cache_leeren():
    app_download_service._cache_url = None
    app_download_service._cache_bis = 0.0


def test_app_installationsseite_ist_oeffentlich_und_zeigt_alle_geraete(client):
    response = client.get("/app")

    assert response.status_code == 200
    assert "ANDROID" in response.text
    assert "IPHONE &amp; IPAD" in response.text
    assert "COMPUTER" in response.text
    assert 'href="/app/android"' in response.text


def test_app_installationsseite_hebt_iphone_hervor(client):
    response = client.get("/app", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"})

    assert response.status_code == 200
    assert '<article class="app-install-card app-install-card--highlight" id="iphone">' in response.text


def test_android_download_leitet_auf_apk_weiter_und_cached(client, monkeypatch):
    _cache_leeren()
    aufrufe = []

    def fake_get(url, *, headers, timeout):
        aufrufe.append((url, headers, timeout))
        return httpx.Response(
            200,
            json={"assets": [{"name": "einsatzcockpit-autoupdate-v2.0.apk", "browser_download_url": "https://cdn.example/app.apk"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(app_download_service.httpx, "get", fake_get)
    first = client.get("/app/android", follow_redirects=False)
    second = client.get("/app/android", follow_redirects=False)

    assert first.status_code == 302 and first.headers["location"] == "https://cdn.example/app.apk"
    assert second.status_code == 302 and second.headers["location"] == "https://cdn.example/app.apk"
    assert len(aufrufe) == 1
    assert aufrufe[0][1]["User-Agent"] == "Einsatzcockpit-App-Download/1.0"
    assert aufrufe[0][2] == 5.0


def test_android_download_faellt_bei_github_fehler_zur_release_seite_zurueck(client, monkeypatch):
    _cache_leeren()

    def fake_get(url, *, headers, timeout):
        raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(app_download_service.httpx, "get", fake_get)
    response = client.get("/app/android", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://github.com/BattloXX/Einsatzcockpit-Android/releases/latest"
