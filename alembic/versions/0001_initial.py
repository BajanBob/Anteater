"""initial schema: observations, findings, sessions

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("ssid", sa.String(64)),
        sa.Column("channel", sa.String(16)),
        sa.Column("freq_mhz", sa.Float()),
        sa.Column("rssi", sa.Float()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("cell", sa.String(12)),
        sa.Column("vendor", sa.String(128)),
        sa.Column("crypt", sa.String(64)),
        sa.Column("session", sa.String(32)),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", "ts"),
    )
    op.create_index("ix_observations_subject_ts", "observations", ["subject", "ts"])
    op.create_index("ix_observations_cell", "observations", ["cell"])

    op.create_table(
        "findings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16)),
        sa.Column("cell", sa.String(12)),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
    )
    for col in ("ts", "engine", "severity", "subject"):
        op.create_index(f"ix_findings_{col}", "findings", [col])

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("started", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended", sa.DateTime(timezone=True)),
        sa.Column("host", sa.String(64)),
        sa.Column("note", sa.String(200)),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # TimescaleDB if present; plain PostgreSQL keeps working without it
        has_ts = bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
        if has_ts:
            op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            op.execute("SELECT create_hypertable('observations', 'ts', if_not_exists => TRUE)")


def downgrade() -> None:
    op.drop_table("sessions")
    for col in ("ts", "engine", "severity", "subject"):
        op.drop_index(f"ix_findings_{col}", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_observations_cell", table_name="observations")
    op.drop_index("ix_observations_subject_ts", table_name="observations")
    op.drop_table("observations")
