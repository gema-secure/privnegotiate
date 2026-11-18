"""merge outbox retry and member lifecycle heads

Revision ID: bd65754d2340
Revises: e5a83c7d61f2, e8f301ab4210
Create Date: 2026-11-18 17:49:28.586479

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bd65754d2340'
down_revision: Union[str, None] = ('e5a83c7d61f2', 'e8f301ab4210')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
