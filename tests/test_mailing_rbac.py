from app.seed_data import ROLES
from tests.conftest import all_app_paths
from app.main import app


def test_mailing_roles_seeded():
    codes = {r["code"] for r in ROLES}
    assert {"mailing_admin", "mailing_sender"} <= codes

def test_all_phase2_and_redesign_routes_are_registered():
    paths=all_app_paths(app)
    assert {"/mailing/dashboard","/mailing/tags","/mailing/lists/import-from-incident","/mailing/campaigns/{campaign_id}/schedule","/mailing/campaigns/{campaign_id}/cancel","/mailing/campaigns/{campaign_id}/retry-failed","/mailing/campaigns/{campaign_id}/attachments","/mailing/campaigns/{campaign_id}/attachments/{attachment_id}/delete","/mailing/campaigns/{campaign_id}/test-send"} <= paths
