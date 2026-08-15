"""Atemschutz Phase 1: individuelle Druck- und Warnzustände.

Revision ID: 0201
Revises: 0200
"""
import sqlalchemy as sa

from alembic import op

revision = "0201"
down_revision = "0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("troop_member") as batch:
        batch.add_column(sa.Column("current_press", sa.Float(), nullable=True))
        batch.add_column(sa.Column("objective_press", sa.Float(), nullable=True))
        batch.add_column(sa.Column("objective_press_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("breathing_troop") as batch:
        batch.add_column(sa.Column("warn_two_third_acked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("warn_withdraw_acked_press", sa.Float(), nullable=True))

    op.execute(sa.text("UPDATE troop_member SET current_press = start_press"))

    with op.batch_alter_table("pressure_log") as batch:
        batch.add_column(sa.Column("troop_member_id", sa.BigInteger(), nullable=True))

    bind = op.get_bind()
    members = bind.execute(sa.text(
        "SELECT id, troop_id, member_id FROM troop_member ORDER BY id"
    )).mappings().all()
    by_troop: dict[int, list[dict]] = {}
    for member in members:
        by_troop.setdefault(member["troop_id"], []).append(member)
    logs = bind.execute(sa.text(
        "SELECT id, troop_id, member_id, pressure_bar FROM pressure_log ORDER BY ts, id"
    )).mappings().all()
    for log in logs:
        candidates = by_troop.get(log["troop_id"], [])
        matching = [m for m in candidates if m["member_id"] == log["member_id"]]
        target = matching[0] if matching else (candidates[0] if candidates else None)
        if target is None:
            raise RuntimeError(f"Drucklog {log['id']} hat kein zugehöriges Truppmitglied")
        bind.execute(
            sa.text("UPDATE pressure_log SET troop_member_id = :member_id WHERE id = :log_id"),
            {"member_id": target["id"], "log_id": log["id"]},
        )
        bind.execute(
            sa.text("UPDATE troop_member SET current_press = :pressure WHERE id = :member_id"),
            {"pressure": log["pressure_bar"], "member_id": target["id"]},
        )

    with op.batch_alter_table("pressure_log") as batch:
        batch.alter_column("troop_member_id", existing_type=sa.BigInteger(), nullable=False)
        batch.create_foreign_key(
            "fk_pressure_log_troop_member_id", "troop_member", ["troop_member_id"], ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("pressure_log") as batch:
        batch.drop_constraint("fk_pressure_log_troop_member_id", type_="foreignkey")
        batch.drop_column("troop_member_id")
    with op.batch_alter_table("breathing_troop") as batch:
        batch.drop_column("warn_withdraw_acked_press")
        batch.drop_column("warn_two_third_acked_at")
    with op.batch_alter_table("troop_member") as batch:
        batch.drop_column("objective_press_at")
        batch.drop_column("objective_press")
        batch.drop_column("current_press")
