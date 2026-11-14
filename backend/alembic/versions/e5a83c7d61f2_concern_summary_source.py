"""concern summary source (llm / manual)

Revision ID: e5a83c7d61f2
Revises: d7e2b41c9a05
Create Date: 2026-11-10 00:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a83c7d61f2'
down_revision: Union[str, None] = 'd7e2b41c9a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'concern',
        sa.Column('summary_source', sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('concern', 'summary_source')
