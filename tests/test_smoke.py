"""PR 12 – Smoke-Tests: alle neuen Multi-Tenancy-Module müssen importierbar sein."""


def test_core_queries_importable():
    from app.core.queries import visible_incidents_q
    assert callable(visible_incidents_q)


def test_ui_sysadmin_importable():
    from app.routers.ui_sysadmin import _org_stats, router
    assert callable(_org_stats)
    assert router is not None


def test_ui_backup_importable():
    from app.routers.ui_backup import _build_export, _diff_dicts, router
    assert callable(_build_export)
    assert callable(_diff_dicts)


def test_rate_limit_importable():
    from app.core.rate_limit import get_api_key_identifier
    assert callable(get_api_key_identifier)


def test_visible_incidents_q_is_shared():
    """ui_incident._visible_incidents_q must delegate to the shared queries module."""
    from app.core.queries import visible_incidents_q
    from app.routers.ui_incident import _visible_incidents_q
    assert _visible_incidents_q is visible_incidents_q


def test_version_bumped():
    from app.config import settings
    major, minor, *_ = settings.APP_VERSION.split(".")
    assert int(major) >= 2
    assert int(minor) >= 2
