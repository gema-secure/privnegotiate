"""candidate attribute tri-state and revision log

Revision ID: d7e2b41c9a05
Revises: c4d9a1f2b7e3
Create Date: 2026-11-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7e2b41c9a05'
down_revision: Union[str, None] = 'c4d9a1f2b7e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'candidate_attribute',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('negotiation_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('attribute_key', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=24), nullable=False),
        sa.Column('source_detail', sa.String(length=120), nullable=True),
        sa.Column('updated_by_member_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['candidate_id'], ['candidate.id'],
            name=op.f('fk_candidate_attribute_candidate_id_candidate'),
        ),
        sa.ForeignKeyConstraint(
            ['negotiation_id'], ['negotiation.id'],
            name=op.f('fk_candidate_attribute_negotiation_id_negotiation'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_attribute')),
        sa.UniqueConstraint(
            'candidate_id', 'attribute_key', name='uq_attribute_candidate_key'
        ),
    )
    op.create_index(
        'ix_attribute_negotiation_key',
        'candidate_attribute',
        ['negotiation_id', 'attribute_key'],
        unique=False,
    )

    op.create_table(
        'candidate_attribute_revision',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('negotiation_id', sa.Integer(), nullable=False),
        sa.Column('attribute_id', sa.Integer(), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('attribute_key', sa.String(length=64), nullable=False),
        sa.Column('previous_state', sa.String(length=16), nullable=True),
        sa.Column('new_state', sa.String(length=16), nullable=False),
        sa.Column('previous_source', sa.String(length=24), nullable=True),
        sa.Column('new_source', sa.String(length=24), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('changed_by_member_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['attribute_id'], ['candidate_attribute.id'],
            name=op.f('fk_candidate_attribute_revision_attribute_id_candidate_attribute'),
        ),
        sa.ForeignKeyConstraint(
            ['negotiation_id'], ['negotiation.id'],
            name=op.f('fk_candidate_attribute_revision_negotiation_id_negotiation'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_attribute_revision')),
    )
    op.create_index(
        'ix_attribute_revision_candidate',
        'candidate_attribute_revision',
        ['candidate_id', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_attribute_revision_candidate', table_name='candidate_attribute_revision'
    )
    op.drop_table('candidate_attribute_revision')
    op.drop_index('ix_attribute_negotiation_key', table_name='candidate_attribute')
    op.drop_table('candidate_attribute')
