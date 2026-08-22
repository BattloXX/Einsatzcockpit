from app.services.mailing_service import mailing_effective_enabled, mailing_system_enabled


def test_toggle_functions_are_exposed():
    assert callable(mailing_system_enabled) and callable(mailing_effective_enabled)
