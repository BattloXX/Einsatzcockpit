"""Nach alembic upgrade head: eigene Tabellen, UTC-Felder und Widerrufsindex."""
import os

import pytest
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")


def test_0326_admin_mcp_schema():
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    try:
        schema = inspect(engine)
        for table in ("admin_mcp_oauth_client", "admin_mcp_oauth_code", "admin_mcp_oauth_token"):
            columns = {c["name"]: c for c in schema.get_columns(table)}
            assert "org_id" not in columns
            if table.endswith(("code", "token")):
                assert not columns["session_epoch"]["nullable"]
                assert columns["mfa_at"]["nullable"]
                assert any(fk["referred_table"] == "user" for fk in schema.get_foreign_keys(table))
        assert any(i["column_names"] == ["family_id"] for i in schema.get_indexes("admin_mcp_oauth_token"))
    finally:
        engine.dispose()
