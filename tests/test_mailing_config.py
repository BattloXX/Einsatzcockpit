from app.models.mailing import MailingConfig


def test_config_requires_enabled_and_credentials():
    cfg = MailingConfig(org_id=1, enabled=True, resend_api_key_enc="encrypted", from_addr="mail@example.at")
    assert cfg.is_fully_configured
