from pathlib import Path
def test_audit_view_exposes_mailing_and_role_scopes():
    text=Path("app/templates/admin/audit.html").read_text(); assert "scope=mailing" in text and "scope=roles" in text
