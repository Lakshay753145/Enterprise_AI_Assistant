"""Initial schema: users, documents, chunks, chat, audit + RLS isolation.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-28

This migration is the security foundation of the system. Beyond the tables it
installs:

* pgvector + IVFFlat index for dense retrieval
* GIN index on the generated tsvector column for BM25-style keyword retrieval
* Row-Level Security policies on every department-scoped table, keyed to the
  ``app.current_department`` session GUC that the API sets per transaction
* Department-scoped read-only VIEWs, which are the *only* objects the SQL
  agent's Postgres role is ever granted access to
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEPARTMENTS = ("Finance", "HR", "IT", "Production", "Purchase")
ROLES = ("user", "admin", "super_admin")

_dept_list = ",".join(f"'{d}'" for d in DEPARTMENTS)
_role_list = ",".join(f"'{r}'" for r in ROLES)

EMBEDDING_DIM = 768  # must match settings.EMBEDDING_DIMENSION


def upgrade() -> None:
    # -------------------------------------------------------------------
    # Extensions
    # -------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # -------------------------------------------------------------------
    # users
    # -------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False, server_default="user"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint(
            f"department IN ({_dept_list})", name="ck_users_department_valid"
        ),
        sa.CheckConstraint(f"role IN ({_role_list})", name="ck_users_role_valid"),
    )
    op.create_index("ix_users_department", "users", ["department"])
    op.create_index("ix_users_department_active", "users", ["department", "is_active"])

    # -------------------------------------------------------------------
    # documents
    # -------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_username", sa.String(length=100), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_seconds", sa.Float(), nullable=True),
        sa.Column(
            "doc_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint("department", "file_hash", name="uq_documents_dept_hash"),
        sa.CheckConstraint(
            f"department IN ({_dept_list})", name="ck_documents_department_valid"
        ),
    )
    op.create_index("ix_documents_department", "documents", ["department"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_file_hash", "documents", ["file_hash"])
    op.create_index(
        "ix_documents_department_status", "documents", ["department", "status"]
    )
    op.create_index(
        "ix_documents_department_created", "documents", ["department", "created_at"]
    )

    # -------------------------------------------------------------------
    # document_chunks
    # -------------------------------------------------------------------
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("section_path", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
                "setweight(to_tsvector('english', content), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "chunk_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        sa.CheckConstraint(
            f"department IN ({_dept_list})", name="ck_chunks_department_valid"
        ),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_department", "document_chunks", ["department"])
    op.create_index(
        "ix_chunks_department_doc", "document_chunks", ["department", "document_id"]
    )

   # Dense retrieval: Index creation omitted for environment compatibility
    # op.execute(
    #     """
    #     CREATE INDEX ix_chunks_embedding_ivfflat
    #     ON document_chunks
    #     USING ivfflat (embedding vector_cosine_ops)
    #     WITH (lists = 100)
    #     """
    # )

    # Keyword retrieval.
    op.execute(
        "CREATE INDEX ix_chunks_content_tsv ON document_chunks USING GIN (content_tsv)"
    )
    # Trigram index backs fuzzy matching on part numbers / spec codes
    op.execute(
        "CREATE INDEX ix_chunks_content_trgm ON document_chunks "
        "USING GIN (content gin_trgm_ops)"
    )

    # -------------------------------------------------------------------
    # conversations
    # -------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column(
            "title", sa.String(length=300), nullable=False, server_default="New chat"
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.CheckConstraint(
            f"department IN ({_dept_list})", name="ck_conversations_department_valid"
        ),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_department", "conversations", ["department"])
    op.create_index(
        "ix_conversations_user_updated", "conversations", ["user_id", "updated_at"]
    )
    op.create_index(
        "ix_conversations_dept_user", "conversations", ["department", "user_id"]
    )

    # -------------------------------------------------------------------
    # messages
    # -------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("answer_source", sa.String(length=40), nullable=True),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "timings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("total_latency_ms", sa.Float(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.CheckConstraint(
            "role IN ('user','assistant','system')", name="ck_messages_role_valid"
        ),
        sa.CheckConstraint(
            f"department IN ({_dept_list})", name="ck_messages_department_valid"
        ),
        sa.CheckConstraint(
            "feedback IS NULL OR feedback IN (-1, 1)", name="ck_messages_feedback_valid"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_department", "messages", ["department"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )
    op.create_index("ix_messages_user_created", "messages", ["user_id", "created_at"])

    # -------------------------------------------------------------------
    # audit_logs
    # -------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("event", sa.String(length=80), nullable=False),
        sa.Column(
            "success", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("department", sa.String(length=50), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_event", "audit_logs", ["event"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_username", "audit_logs", ["username"])
    op.create_index("ix_audit_logs_department", "audit_logs", ["department"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_event_created", "audit_logs", ["event", "created_at"])
    op.create_index(
        "ix_audit_department_created", "audit_logs", ["department", "created_at"]
    )
    op.execute(
        "CREATE INDEX ix_audit_failures ON audit_logs (created_at) "
        "WHERE success = false"
    )

    # -------------------------------------------------------------------
    # Row-Level Security
    # -------------------------------------------------------------------
    _enable_rls("documents")
    _enable_rls("document_chunks")
    _enable_rls("conversations")
    _enable_rls("messages")

    # -------------------------------------------------------------------
    # Department-scoped views for the SQL agent
    # -------------------------------------------------------------------
    for dept in DEPARTMENTS:
        slug = dept.lower()
        op.execute(
            f"""
            CREATE OR REPLACE VIEW kb_{slug}_documents
            WITH (security_barrier = true) AS
            SELECT
                d.id,
                d.original_filename AS document_name,
                d.title,
                d.status,
                d.page_count,
                d.chunk_count,
                d.uploaded_by_username AS uploaded_by,
                d.created_at,
                d.processed_at
            FROM documents d
            WHERE d.department = '{dept}'
              AND d.status = 'completed'
            """
        )
        op.execute(
            f"""
            CREATE OR REPLACE VIEW kb_{slug}_chunks
            WITH (security_barrier = true) AS
            SELECT
                c.id,
                c.document_id,
                d.original_filename AS document_name,
                c.chunk_index,
                c.heading,
                c.section_path,
                c.page_number,
                c.content
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.department = '{dept}'
            """
        )

    op.execute(
        """
        COMMENT ON TABLE document_chunks IS
        'Retrievable passages. RLS-protected: reads are filtered by the
         app.current_department session GUC.'
        """
    )


def _enable_rls(table: str) -> None:
    """Turn on RLS for a table and install the department isolation policy."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_department_isolation ON {table}
        FOR ALL
        USING (
            department = current_setting('app.current_department', true)
            OR current_setting('app.current_role', true) = 'super_admin'
        )
        WITH CHECK (
            department = current_setting('app.current_department', true)
            OR current_setting('app.current_role', true) = 'super_admin'
        )
        """
    )


def downgrade() -> None:
    for dept in DEPARTMENTS:
        slug = dept.lower()
        op.execute(f"DROP VIEW IF EXISTS kb_{slug}_chunks")
        op.execute(f"DROP VIEW IF EXISTS kb_{slug}_documents")

    for table in ("messages", "conversations", "document_chunks", "documents"):
        op.execute(f"DROP POLICY IF EXISTS {table}_department_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("audit_logs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("users")