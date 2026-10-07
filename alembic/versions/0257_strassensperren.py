"""Straßensperren-Grundmodell und Routing-Einstellungen.

Revision ID: 0257
Revises: 0256
"""

import sqlalchemy as sa

from alembic import op

revision = "0257"
down_revision = "0256"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    # Wie der TenantScoped-Mixin: nullable, SET NULL, indiziert (Muster 0232).
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("strassensperren_modul_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("org_settings", sa.Column("routing_start_lat", sa.Float(), nullable=True))
    op.add_column("org_settings", sa.Column("routing_start_lng", sa.Float(), nullable=True))
    op.add_column("org_settings", sa.Column("routing_start_label", sa.String(200), nullable=True))
    op.create_table(
        "road_closure",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("street", sa.String(200)),
        sa.Column("from_text", sa.String(200)),
        sa.Column("to_text", sa.String(200)),
        sa.Column("direction", sa.String(20)),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_until", sa.DateTime()),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.Column("cancel_reason", sa.String(500)),
        sa.Column("restriction_type", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("max_weight_t", sa.Float()),
        sa.Column("max_height_m", sa.Float()),
        sa.Column("max_width_m", sa.Float()),
        sa.Column("max_length_m", sa.Float()),
        sa.Column("geometry_geojson", sa.Text()),
        sa.Column("geometry_status", sa.String(20), nullable=False),
        sa.Column("bbox_min_lat", sa.Float()),
        sa.Column("bbox_min_lng", sa.Float()),
        sa.Column("bbox_max_lat", sa.Float()),
        sa.Column("bbox_max_lng", sa.Float()),
        sa.Column("source", sa.String(200)),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("external_source", sa.String(100)),
        sa.Column("external_id", sa.String(200)),
        sa.Column("last_external_update", sa.DateTime()),
        sa.Column("created_via", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_road_closure_org_valid", "road_closure", ["org_id", "valid_from", "valid_until"])
    op.create_table(
        "road_closure_share",
        sa.Column(
            "road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "road_closure_change",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column(
            "road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("field", sa.String(60)),
        sa.Column("before_json", sa.Text()),
        sa.Column("after_json", sa.Text()),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("mcp_tool", sa.String(80)),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_road_closure_change_road_closure_id", "road_closure_change", ["road_closure_id"])
    op.create_table(
        "incident_route",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("start_lat", sa.Float()),
        sa.Column("start_lng", sa.Float()),
        sa.Column("dest_lat", sa.Float()),
        sa.Column("dest_lng", sa.Float()),
        sa.Column("closure_fingerprint", sa.String(64)),
        sa.Column("route_geojson", sa.Text()),
        sa.Column("route_distance_m", sa.Float()),
        sa.Column("route_duration_s", sa.Float()),
        sa.Column("alternative_status", sa.String(20), nullable=False),
        sa.Column("alternative_route_geojson", sa.Text()),
        sa.Column("alternative_distance_m", sa.Float()),
        sa.Column("alternative_duration_s", sa.Float()),
        sa.Column("alternative_streets_json", sa.Text()),
        sa.Column("detour_distance_m", sa.Float()),
        sa.Column("detour_duration_s", sa.Float()),
        sa.Column("routing_provider", sa.String(20)),
        sa.Column("routing_error", sa.String(500)),
        sa.Column("calculated_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("incident_id", "org_id"),
    )
    op.create_table(
        "incident_road_closure",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column(
            "incident_route_id", sa.BigInteger(), sa.ForeignKey("incident_route.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="SET NULL")),
        sa.Column("relevance", sa.String(20), nullable=False),
        sa.Column("distance_to_route_m", sa.Float()),
        sa.Column("distance_to_destination_m", sa.Float()),
        sa.Column("title_snapshot", sa.String(200)),
        sa.Column("restriction_type_snapshot", sa.String(30)),
        sa.Column("geometry_snapshot", sa.Text()),
        sa.Column("geometry_status_snapshot", sa.String(20)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_incident_road_closure_incident_route_id", "incident_road_closure", ["incident_route_id"])
    op.create_index("ix_incident_road_closure_incident_id", "incident_road_closure", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_incident_road_closure_incident_id", table_name="incident_road_closure")
    op.drop_index("ix_incident_road_closure_incident_route_id", table_name="incident_road_closure")
    op.drop_table("incident_road_closure")
    op.drop_table("incident_route")
    op.drop_index("ix_road_closure_change_road_closure_id", table_name="road_closure_change")
    op.drop_table("road_closure_change")
    op.drop_table("road_closure_share")
    op.drop_index("ix_road_closure_org_valid", table_name="road_closure")
    op.drop_table("road_closure")
    op.drop_column("org_settings", "routing_start_label")
    op.drop_column("org_settings", "routing_start_lng")
    op.drop_column("org_settings", "routing_start_lat")
    op.drop_column("org_settings", "strassensperren_modul_aktiv")
