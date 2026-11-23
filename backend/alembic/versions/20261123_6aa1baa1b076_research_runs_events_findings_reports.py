"""Research runs: runs, run_events, sub_questions, findings, reports; usage_events.run_id.

The LangGraph checkpoint tables and the Procrastinate job queue are created by
python -m anvesha.migrate (they manage their own schemas), not by Alembic.

Revision ID: 6aa1baa1b076
Revises: ac8b94f08a17
Create Date: 2026-11-23 23:43:34.825520
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '6aa1baa1b076'
down_revision: str | None = 'ac8b94f08a17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('schema_components',
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_schema_components'))
    )
    op.create_table('runs',
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('depth', sa.String(length=16), nullable=False),
    sa.Column('autopilot', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('max_cost_usd', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('max_tokens', sa.Integer(), nullable=True),
    sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('pending_decision', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("depth IN ('quick', 'standard', 'deep')", name=op.f('ck_runs_depth')),
    sa.CheckConstraint("status IN ('queued', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled')", name=op.f('ck_runs_status')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_runs_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_runs_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_runs_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runs'))
    )
    op.create_index('ix_runs_project_id_created_at', 'runs', ['project_id', 'created_at'], unique=False)
    op.create_index('uq_runs_project_id_active', 'runs', ['project_id'], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'awaiting_approval')"))
    op.create_table('reports',
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=False),
    sa.Column('citations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('complete', 'incomplete')", name=op.f('ck_reports_status')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_reports_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_reports_run_id_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_reports_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    sa.UniqueConstraint('project_id', 'version', name=op.f('uq_reports_project_id')),
    sa.UniqueConstraint('run_id', name=op.f('uq_reports_run_id'))
    )
    op.create_table('run_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=48), nullable=False),
    sa.Column('agent', sa.String(length=64), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_run_events_run_id_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_run_events_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_events'))
    )
    op.create_index('ix_run_events_run_id_id', 'run_events', ['run_id', 'id'], unique=False)
    op.create_table('sub_questions',
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('coverage', sa.String(length=16), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('gaps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('steps', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("coverage IS NULL OR coverage IN ('answered', 'partial', 'not_found')", name=op.f('ck_sub_questions_coverage')),
    sa.CheckConstraint("status IN ('pending', 'researching', 'done', 'failed')", name=op.f('ck_sub_questions_status')),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_sub_questions_run_id_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_sub_questions_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sub_questions')),
    sa.UniqueConstraint('run_id', 'round', 'ordinal', name=op.f('uq_sub_questions_run_id'))
    )
    op.create_table('findings',
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('sub_question_id', sa.UUID(), nullable=True),
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('quote_hash', sa.String(length=64), nullable=False),
    sa.Column('spans', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('judge_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('quote_verified', 'verified', 'rejected')", name=op.f('ck_findings_status')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_findings_project_id_projects'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_findings_run_id_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_findings_source_id_sources'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sub_question_id'], ['sub_questions.id'], name=op.f('fk_findings_sub_question_id_sub_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_findings_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_findings')),
    sa.UniqueConstraint('run_id', 'source_id', 'quote_hash', name=op.f('uq_findings_run_id'))
    )
    op.create_index('ix_findings_project_id_created_at', 'findings', ['project_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_findings_source_id'), 'findings', ['source_id'], unique=False)
    op.create_index(op.f('ix_findings_sub_question_id'), 'findings', ['sub_question_id'], unique=False)
    op.add_column('usage_events', sa.Column('run_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_usage_events_run_id'), 'usage_events', ['run_id'], unique=False)
    op.create_foreign_key(op.f('fk_usage_events_run_id_runs'), 'usage_events', 'runs', ['run_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_usage_events_run_id_runs'), 'usage_events', type_='foreignkey')
    op.drop_index(op.f('ix_usage_events_run_id'), table_name='usage_events')
    op.drop_column('usage_events', 'run_id')
    op.drop_index(op.f('ix_findings_sub_question_id'), table_name='findings')
    op.drop_index(op.f('ix_findings_source_id'), table_name='findings')
    op.drop_index('ix_findings_project_id_created_at', table_name='findings')
    op.drop_table('findings')
    op.drop_table('sub_questions')
    op.drop_index('ix_run_events_run_id_id', table_name='run_events')
    op.drop_table('run_events')
    op.drop_table('reports')
    op.drop_index('uq_runs_project_id_active', table_name='runs', postgresql_where=sa.text("status IN ('queued', 'running', 'awaiting_approval')"))
    op.drop_index('ix_runs_project_id_created_at', table_name='runs')
    op.drop_table('runs')
    op.drop_table('schema_components')
