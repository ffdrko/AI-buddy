"""0002 soft delete for documents (Phase 2)

Revision ID: 0002_document_deleted_at
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0002_document_deleted_at"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_deleted_at", table_name="documents")
    op.drop_column("documents", "deleted_at")
