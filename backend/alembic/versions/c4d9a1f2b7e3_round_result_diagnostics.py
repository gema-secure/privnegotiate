"""round_result 增加 P5 诊断审计字段

Revision ID: c4d9a1f2b7e3
Revises: 8b118e1719fe
Create Date: 2026-10-23 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d9a1f2b7e3'
down_revision: Union[str, None] = '8b118e1719fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'round_result',
        sa.Column('diagnostics', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column('round_result', 'diagnostics')
