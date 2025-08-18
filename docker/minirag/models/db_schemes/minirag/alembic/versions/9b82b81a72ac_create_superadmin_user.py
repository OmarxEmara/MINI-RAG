from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "9b82b81a72ac"
down_revision = "5bb711f9b2e1"
branch_labels = None
depends_on = None


def upgrade():
    # Ensure pgcrypto exists (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # Insert a Super Admin. Change email/password as you prefer.
    op.execute("""
        INSERT INTO users (email, password_hash, is_super_admin, is_active)
        VALUES (
            'omar@example.com',
            crypt('OmarEmara123', gen_salt('bf')),
            TRUE,
            TRUE
        )
        ON CONFLICT (email) DO NOTHING;
    """)


def downgrade():
    op.execute("DELETE FROM users WHERE email = 'omar@example.com';")