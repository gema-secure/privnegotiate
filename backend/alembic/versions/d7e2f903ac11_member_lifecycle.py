"""Member lifecycle, frozen roster, and scoped removal authorization.

Revision ID: d7e2f903ac11
Revises: c4d9a1f2b7e3
"""
from alembic import op
import sqlalchemy as sa

revision = "d7e2f903ac11"
down_revision = "c4d9a1f2b7e3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("negotiation", sa.Column("membership_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("negotiation", sa.Column("membership_frozen", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("negotiation", sa.Column("closing_job_version", sa.Integer(), nullable=True))
    op.add_column("member", sa.Column("membership_status", sa.String(24), nullable=False, server_default="ACTIVE"))
    op.add_column("member", sa.Column("response_status", sa.String(24), nullable=False, server_default="ACTIVE"))
    op.add_column("member", sa.Column("token_revoked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE negotiation SET membership_frozen = 1 WHERE round_no > 0 OR status IN ('ASKING', 'ANSWERING', 'CLOSING', 'COMPUTING', 'UPDATING', 'PRIVATE_CONFIRM', 'BEST_EFFORT', 'CONCERN_COLLECTING', 'FINALIZED', 'CLOSED_WITH_CONCERN', 'CLOSED_UNCONFIRMED')")
    # 旧 active=false 没有退出授权记录，安全迁移为整令牌撤销而非伪造自愿离开。
    op.execute("UPDATE member SET token_revoked = 1 WHERE active = 0")
    op.create_table(
        "removal_request",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("negotiation_id", sa.Integer(), sa.ForeignKey("negotiation.id"), nullable=False),
        sa.Column("target_member_id", sa.Integer(), sa.ForeignKey("member.id"), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("member.id"), nullable=False),
        sa.Column("membership_version", sa.Integer(), nullable=False),
        sa.Column("eligible_member_ids", sa.JSON(), nullable=False),
        sa.Column("votes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("removal_request")
    for name in ("token_revoked", "response_status", "membership_status"):
        op.drop_column("member", name)
    op.drop_column("negotiation", "membership_frozen")
    op.drop_column("negotiation", "closing_job_version")
    op.drop_column("negotiation", "membership_version")
