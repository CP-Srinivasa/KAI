"""create canonical_documents table

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canonical_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("source_id", sa.String(36), nullable=True, index=True),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=True, index=True),
        sa.Column(
            "document_type", sa.String(50), nullable=False, server_default="unknown", index=True
        ),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("url", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("language", sa.String(10), nullable=True, index=True),
        sa.Column("market_scope", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("cleaned_text", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("sentiment_label", sa.String(20), nullable=True, index=True),
        sa.Column("sentiment_score", sa.Float, nullable=True),
        sa.Column("relevance_score", sa.Float, nullable=True),
        sa.Column("impact_score", sa.Float, nullable=True),
        sa.Column("credibility_score", sa.Float, nullable=True),
        sa.Column("is_duplicate", sa.Boolean, nullable=False, server_default="false", index=True),
        sa.Column("is_analyzed", sa.Boolean, nullable=False, server_default="false", index=True),
        sa.Column("entity_mentions", JSON, nullable=True),
        sa.Column("entities", JSON, nullable=True),
        sa.Column("tickers", JSON, nullable=True),
        sa.Column("crypto_assets", JSON, nullable=True),
        sa.Column("people", JSON, nullable=True),
        sa.Column("organizations", JSON, nullable=True),
        sa.Column("tags", JSON, nullable=True),
        sa.Column("topics", JSON, nullable=True),
        sa.Column("categories", JSON, nullable=True),
        sa.Column("youtube_meta", JSON, nullable=True),
        sa.Column("podcast_meta", JSON, nullable=True),
        sa.Column("metadata", JSON, nullable=True),
    )
    op.create_index(
        "ix_canonical_documents_source_published",
        "canonical_documents",
        ["source_id", "published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_documents_source_published", "canonical_documents")
    op.drop_table("canonical_documents")
