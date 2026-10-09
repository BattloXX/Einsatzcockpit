"""Schema/data coverage for the org_admin to admin role migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0264_merge_org_admin_role.py"
    spec = importlib.util.spec_from_file_location("migration_0264", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_merges_assignments_without_duplicate_user_roles(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0264.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO role (id, code, label) VALUES "
                             "(1, 'admin', 'old admin'), (2, 'org_admin', 'old org admin')"))
        conn.execute(sa.text("INSERT INTO user "
                             "(id, username, display_name, active, is_device, auth_provider, created_at, "
                             "failed_login_count) VALUES "
                             "(1, 'only-old', 'Only old', 1, 0, 'local', CURRENT_TIMESTAMP, 0), "
                             "(2, 'both', 'Both', 1, 0, 'local', CURRENT_TIMESTAMP, 0)"))
        conn.execute(sa.text("INSERT INTO user_role (user_id, role_id) VALUES (1, 2), (2, 1), (2, 2)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        assert conn.execute(sa.text("SELECT label FROM role WHERE code = 'admin'")).scalar_one() == \
            "Organisations-Administrator"
        assert conn.execute(sa.text("SELECT COUNT(*) FROM role WHERE code = 'org_admin'")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT user_id, role_id FROM user_role ORDER BY user_id, role_id")).all() == [
            (1, 1), (2, 1),
        ]
