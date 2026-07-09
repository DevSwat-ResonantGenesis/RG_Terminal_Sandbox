"""Create terminal_sessions table

Revision ID: 001_create_terminal_sessions
Revises: None
Create Date: 2026-07-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_create_terminal_sessions'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'terminal_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('terminal_id', sa.String(128), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('container_id', sa.String(128), nullable=True),
        sa.Column('container_name', sa.String(128), nullable=True),
        sa.Column('git_repo_url', sa.String(512), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='creating'),
        sa.Column('cpu_limit', sa.String(16), nullable=True),
        sa.Column('memory_limit_mb', sa.Integer(), nullable=True),
        sa.Column('idle_timeout_seconds', sa.Integer(), nullable=True),
        sa.Column('transcript_storage_key', sa.String(512), nullable=True),
        sa.Column('transcript_storage_bucket', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('terminal_id', name='uq_terminal_sessions_terminal_id'),
    )
    op.create_index('ix_terminal_sessions_terminal_id', 'terminal_sessions', ['terminal_id'])
    op.create_index('ix_terminal_sessions_user_id', 'terminal_sessions', ['user_id'])
    op.create_index('ix_terminal_sessions_org_id', 'terminal_sessions', ['org_id'])
    op.create_index('ix_terminal_sessions_project_id', 'terminal_sessions', ['project_id'])
    op.create_index('ix_terminal_sessions_status', 'terminal_sessions', ['status'])


def downgrade() -> None:
    op.drop_table('terminal_sessions')
