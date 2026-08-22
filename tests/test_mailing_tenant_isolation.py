from app.core.tenant import _TENANT_TABLE_NAMES


def test_all_owned_tables_are_tenant_scoped():
    assert {
        "mailing_template",
        "mailing_recipient_list",
        "mailing_recipient_list_entry",
        "mailing_campaign",
        "mailing_queue_item",
    } <= _TENANT_TABLE_NAMES
    assert "mailing_config" not in _TENANT_TABLE_NAMES
