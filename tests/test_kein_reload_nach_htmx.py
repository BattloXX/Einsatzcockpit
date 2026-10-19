"""Gezielte Swaps nach HTMX-Aktionen und Einsatz-WebSocket-Events."""
import re
from pathlib import Path
from unittest.mock import patch

from tests.test_incident_subnav import _login, _setup

ROOT = Path(__file__).resolve().parents[1]
HTMX_HANDLER = re.compile(
    r'''(?:\bhx-on[^\s=]*|@htmx:after-request)\s*=\s*(["'])(.*?)\1''',
    re.DOTALL | re.IGNORECASE,
)


def test_htmx_handler_ohne_location_reload():
    violations = []
    for path in (ROOT / "app/templates").rglob("*.html"):
        for match in HTMX_HANDLER.finditer(path.read_text()):
            if re.search(r"location\s*\.\s*reload", match[2]):
                violations.append(f"{path.relative_to(ROOT)}: {match[0]}")
    assert not violations, "\n".join(violations)


def test_app_js_ohne_location_reload():
    assert not re.search(r"location\s*\.\s*reload", (ROOT / "app/static/js/app.js").read_text())


def test_funkjournal_swaps_und_broadcasts(client):
    username, _, incident_id, _ = _setup()
    assert _login(client, username, "Test1234!").status_code in (302, 303)
    url = f"/einsatz/{incident_id}/funkjournal"
    csrf = client.cookies.get("ec_csrf")
    with patch("app.routers.ui_incident.run_async") as broadcast:
        response = client.post(url, data={
            "direction": "in", "message": "Funkprobe", "is_request": "true", "_csrf": csrf,
        })
        assert response.status_code == 204
        assert broadcast.call_args.args[1:] == (incident_id, {"type": "funkjournal_changed"})
        page = client.get(url)
        assert page.status_code == 200
        assert 'id="fjList"' in page.text
        assert 'data-fj-search="' in page.text
        assert 'id="fj-search"' in page.text
        forms = re.findall(r"<form\b.*?</form>", page.text, re.DOTALL)
        forms = [form for form in forms if f'hx-post="{url}' in form]
        assert len(forms) == 3
        assert all("refreshFjList()" in form and "location.reload" not in form for form in forms)
        for suffix in ("erledigt", "lage"):
            action = re.search(rf'hx-post="([^"]+/{suffix})"', page.text)
            assert action is not None
            assert client.post(action[1], data={"_csrf": csrf}).status_code == 204
        assert broadcast.call_count == 3
    updated = client.get(url)
    assert "fj-row--handled" in updated.text
    assert '<span class="fj-lage-badge">' in updated.text
    assert '<span class="fj-open-badge">0</span>' in updated.text


def test_topnav_leader_swap_auch_nach_freitext(client):
    username, _, incident_id, _ = _setup()
    assert _login(client, username, "Test1234!").status_code in (302, 303)
    url = f"/einsatz/{incident_id}"
    page = client.get(url)
    assert page.status_code == 200
    assert 'id="topnavLeader"' in page.text
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"{url}/einsatzleiter-mitglied-neu", data={
        "full_name": "Freitext EL", "_csrf": csrf,
    }).status_code == 204
    page = client.get(url)
    leader = page.text.split('id="topnavLeader"', 1)[1].split('class="topnav__user"', 1)[0]
    assert "Freitext EL" in leader
    assert 'x-data="{open:false}"' in leader
    assert "location.reload" not in leader
    assert client.post(f"{url}/einsatzleiter-mitglied", data={
        "member_id": "", "_csrf": csrf,
    }).status_code == 204
    assert "Freitext EL" not in client.get(url).text
