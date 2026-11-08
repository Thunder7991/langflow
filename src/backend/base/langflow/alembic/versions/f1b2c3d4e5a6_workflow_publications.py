"""Independent publication snapshots, online pointer and execution references.

Revision ID: f1b2c3d4e5a6
Revises: e5a7c9b1d3f2
Phase: EXPAND
"""

import sqlalchemy as sa
from alembic import op

revision = "f1b2c3d4e5a6"
down_revision = "e5a7c9b1d3f2"
branch_labels = None
depends_on = None


def upgrade():
    # 只新增结构, 历史 PUBLIC 流程不会被自动发布。
    op.create_table(
        "workflow_publication",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("flow_id", sa.Uuid(), sa.ForeignKey("flow.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_version_token", sa.Uuid(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("flow_id", "version_number", name="uq_workflow_publication_version"),
        sa.CheckConstraint("version_number > 0", name=sa.sql.naming.conv("ck_workflow_publication_version_positive")),
    )
    op.create_index("ix_workflow_publication_flow_id", "workflow_publication", ["flow_id"])
    op.create_table(
        "workflow_publication_state",
        sa.Column("flow_id", sa.Uuid(), sa.ForeignKey("flow.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("workflow_publication.id", ondelete="RESTRICT")),
        sa.Column("token", sa.Uuid(), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "workflow_publication_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "publication_id", sa.Uuid(), sa.ForeignKey("workflow_publication.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=sa.sql.naming.conv("ck_workflow_publication_run_status"),
        ),
    )
    op.create_index("ix_workflow_publication_run_publication_id", "workflow_publication_run", ["publication_id"])
    op.create_index("ix_workflow_publication_run_created_by", "workflow_publication_run", ["created_by"])


def downgrade():
    # 发布记录是线上和审计依赖, 禁止通过降级静默销毁。
    message = "Publication history must be preserved; use a reviewed forward migration."
    raise RuntimeError(message)
