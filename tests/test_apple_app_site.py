"""AASA wird ohne Login und nur mit gueltiger Apple-Team-ID ausgeliefert."""

import pytest

from app.config import settings

PATH = "/.well-known/apple-app-site-association"


@pytest.mark.parametrize("team_id", ["", "ABCDE1234", "abcde12345", "ABCDE1234!", "ABCDE12345\n"])
def test_missing_or_invalid_team_id_returns_404(client, monkeypatch, team_id):
    monkeypatch.setattr(settings, "APPLE_TEAM_ID", team_id)
    response = client.get(PATH, follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "public, max-age=300"


def test_valid_team_id_is_public_without_session(client, monkeypatch):
    monkeypatch.setattr(settings, "APPLE_TEAM_ID", "ABCDE12345")
    client.cookies.clear()
    response = client.get(PATH, follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.json() == {
        "applinks": {
            "details": [{
                "appIDs": ["ABCDE12345.cloud.einsatzleiter.app"],
                "components": [
                    {"/": "/api/*", "exclude": True},
                    {"/": "/static/*", "exclude": True},
                    {"/": "/*"},
                ],
            }],
        },
        "webcredentials": {"apps": ["ABCDE12345.cloud.einsatzleiter.app"]},
    }
