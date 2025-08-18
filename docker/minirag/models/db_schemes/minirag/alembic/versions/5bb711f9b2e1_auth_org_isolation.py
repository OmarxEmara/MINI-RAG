from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "5bb711f9b2e1"
down_revision = "fee4cd54bd38"  # set to previous revision id if you already have one here
branch_labels = None
depends_on = None

def upgrade():
    # 1) Extensions (safe if already installed; may require superuser)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS citext;")    # CITEXT email

    # 2) organizations
    op.create_table(
        "organizations",
        sa.Column("org_id", sa.Integer, primary_key=True),
        sa.Column("org_uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("metadata", postgresql.JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # 3) users
    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer, primary_key=True),
        sa.Column("user_uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_super_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # 4) roles + memberships
    # Make org_role enum idempotent
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_role') THEN
            CREATE TYPE org_role AS ENUM ('ADMIN', 'USER');
        END IF;
    END$$;
    """)

    op.create_table(
        "user_memberships",
        sa.Column("membership_id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", postgresql.ENUM("ADMIN", "USER", name="org_role", create_type=False), nullable=False),
        sa.UniqueConstraint("user_id", "org_id", name="uq_user_org"),
    )

    # 5) refresh tokens (optional but recommended)
    op.create_table(
        "refresh_tokens",
        sa.Column("token_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # 6) projects → organizations (nullable add, backfill, then NOT NULL)
    op.add_column("projects", sa.Column("project_org_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_projects_org",
        source_table="projects",
        referent_table="organizations",
        local_cols=["project_org_id"],
        remote_cols=["org_id"],
    )

    # Backfill: create a default org and attach existing projects
    op.execute("""
        INSERT INTO organizations (name)
        VALUES ('Default Organization')
        ON CONFLICT (name) DO NOTHING;
    """)
    op.execute("""
        WITH def_org AS (
            SELECT org_id FROM organizations WHERE name = 'Default Organization' LIMIT 1
        )
        UPDATE projects
        SET project_org_id = (SELECT org_id FROM def_org)
        WHERE project_org_id IS NULL;
    """)

    # Enforce NOT NULL
    op.alter_column("projects", "project_org_id", nullable=False)

def downgrade():
    # Drop FK/column from projects first
    op.drop_constraint("fk_projects_org", "projects", type_="foreignkey")
    op.drop_column("projects", "project_org_id")

    # refresh tokens
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    # memberships + enum
    op.drop_table("user_memberships")
    # Drop enum only if it exists and no longer used
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_role') THEN DROP TYPE org_role; END IF; END$$;")

    # users/organizations
    op.drop_table("users")
    op.drop_table("organizations")
