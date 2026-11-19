"""Schedule outbox retries with exponential backoff."""
from alembic import op
import sqlalchemy as sa

revision = "e8f301ab4210"
down_revision = "d7e2f903ac11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outbox", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_outbox_retry", "outbox", ["status", "next_retry_at", "id"])


def downgrade():
    op.drop_index("ix_outbox_retry", table_name="outbox")
    op.drop_column("outbox", "next_retry_at")
