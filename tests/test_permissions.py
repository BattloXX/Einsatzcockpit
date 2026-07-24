"""Regressionstest für den has_role()/require_role() "system_admin"-Bug:
beide Funktionen unionierten die angefragten Rollen früher unconditional mit
{"admin", "org_admin"} - dadurch bestand ein has_role(user, "system_admin")
(gedacht als exakter Cross-Org-Check) auch für einen gewöhnlichen org_admin/
admin-Nutzer, der NICHT system_admin ist. Siehe app/core/permissions.py.
"""
import pytest
from fastapi import HTTPException

from app.core.permissions import (
    has_role,
    is_system_admin,
    require_role,
    same_org_or_system_admin,
)


class _FakeRole:
    def __init__(self, code):
        self.code = code


class _FakeUser:
    def __init__(self, org_id, roles):
        self.org_id = org_id
        self.roles = [_FakeRole(r) for r in roles]


class _FakeState:
    def __init__(self, user):
        self.user = user


class _FakeRequest:
    def __init__(self, user):
        self.state = _FakeState(user)


def test_has_role_system_admin_exclusive_for_org_admin():
    user = _FakeUser(1, roles=("org_admin",))
    assert has_role(user, "system_admin") is False


def test_has_role_system_admin_exclusive_for_admin():
    user = _FakeUser(1, roles=("admin",))
    assert has_role(user, "system_admin") is False


def test_has_role_system_admin_true_for_real_system_admin():
    user = _FakeUser(1, roles=("system_admin",))
    assert has_role(user, "system_admin") is True


def test_has_role_org_admin_still_satisfies_lower_role_checks():
    """Die generelle Union bleibt fuer NICHT-system_admin-Checks bestehen -
    ein org_admin soll weiterhin z.B. incident_leader-Rechte haben."""
    user = _FakeUser(1, roles=("org_admin",))
    assert has_role(user, "incident_leader") is True


def test_has_role_mixed_system_admin_org_admin_request_unaffected():
    """Bewusst gemischte Checks (z.B. require_role('system_admin','org_admin')
    in ui_ai_prompts.py) sollen unveraendert fuer org_admin greifen."""
    user = _FakeUser(1, roles=("org_admin",))
    assert has_role(user, "system_admin", "org_admin") is True


def test_require_role_system_admin_rejects_org_admin():
    user = _FakeUser(1, roles=("org_admin",))
    dep = require_role("system_admin")
    with pytest.raises(HTTPException) as exc_info:
        dep(_FakeRequest(user))
    assert exc_info.value.status_code == 403


def test_require_role_system_admin_accepts_real_system_admin():
    user = _FakeUser(1, roles=("system_admin",))
    dep = require_role("system_admin")
    assert dep(_FakeRequest(user)) is user


def test_require_role_mixed_system_admin_org_admin_still_accepts_org_admin():
    user = _FakeUser(1, roles=("org_admin",))
    dep = require_role("system_admin", "org_admin")
    assert dep(_FakeRequest(user)) is user


def test_is_system_admin_helper_matches_fixed_has_role():
    sysadmin = _FakeUser(1, roles=("system_admin",))
    org_admin = _FakeUser(1, roles=("org_admin",))
    assert is_system_admin(sysadmin) is True
    assert is_system_admin(org_admin) is False


def test_same_org_or_system_admin_rejects_org_admin_for_foreign_org():
    org_admin = _FakeUser(1, roles=("org_admin",))
    assert same_org_or_system_admin(org_admin, 2) is False
    assert same_org_or_system_admin(org_admin, 1) is True
