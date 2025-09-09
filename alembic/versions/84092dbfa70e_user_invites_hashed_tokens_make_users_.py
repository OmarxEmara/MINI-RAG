from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "84092dbfa70e"       # keep whatever Alembic generated for you
down_revision = "85db78224fa6"  # your current head
branch_labels = None
depends_on = None


def upgrade():
    # Ensure extensions (safe if already installed)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext;")

    bind = op.get_bind()
    insp = sa.inspect(bind)

    # organizations (if missing)
    if not insp.has_table("organizations"):
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

    # org_role enum (idempotent)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_role') THEN
            CREATE TYPE org_role AS ENUM ('ADMIN', 'USER');
        END IF;
    END$$;
    """)

    # users (if missing). Make password_hash nullable from the start.
    if not insp.has_table("users"):
        op.create_table(
            "users",
            sa.Column("user_id", sa.Integer, primary_key=True),
            sa.Column("user_uuid", postgresql.UUID(as_uuid=True), nullable=False,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text, nullable=True),  # nullable for invite flow
            sa.Column("is_super_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
        )

    # user_memberships (if missing)
    if not insp.has_table("user_memberships"):
        op.create_table(
            "user_memberships",
            sa.Column("membership_id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer,
                      sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("org_id", sa.Integer,
                      sa.ForeignKey("organizations.org_id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", postgresql.ENUM("ADMIN", "USER", name="org_role", create_type=False), nullable=False),
            sa.UniqueConstraint("user_id", "org_id", name="uq_user_org"),
        )

    # refresh_tokens (if missing)
    if not insp.has_table("refresh_tokens"):
        op.create_table(
            "refresh_tokens",
            sa.Column("token_id", postgresql.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", sa.Integer,
                      sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        )
        op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # user_invites
    if not insp.has_table("user_invites"):
        op.create_table(
            "user_invites",
            sa.Column("invite_id", sa.BigInteger(), primary_key=True),
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=False, server_default="SET_PASSWORD"),
            sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                      nullable=False, server_default=sa.text("now()")),
            sa.Column("created_by_user_id", sa.Integer(),
                      sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_user_invites_user_id", "user_invites", ["user_id"])
        op.create_index("ix_user_invites_token_hash", "user_invites", ["token_hash"])
        op.create_index("ix_user_invites_expires_at", "user_invites", ["expires_at"])

    # make sure password_hash is nullable (works whether users was just created or existed)
    try:
        op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)
    except Exception:
        # if already nullable (e.g., table created above), ignore
        pass

    # safety: active users must have a password (skip if it already exists)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_active_users_have_password'
              AND conrelid = 'users'::regclass
        ) THEN
            ALTER TABLE users ADD CONSTRAINT ck_active_users_have_password
            CHECK ((is_active = false) OR (password_hash IS NOT NULL));
        END IF;
    END$$;
    """)


def downgrade():
    # Drop check constraint if present
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_active_users_have_password'
              AND conrelid = 'users'::regclass
        ) THEN
            ALTER TABLE users DROP CONSTRAINT ck_active_users_have_password;
        END IF;
    END$$;
    """)

    # restore NOT NULL on password_hash
    try:
        op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    except Exception:
        pass

    # Drop invites
    op.drop_index("ix_user_invites_expires_at", table_name="user_invites")
    op.drop_index("ix_user_invites_token_hash", table_name="user_invites")
    op.drop_index("ix_user_invites_user_id", table_name="user_invites")
    op.drop_table("user_invites")
