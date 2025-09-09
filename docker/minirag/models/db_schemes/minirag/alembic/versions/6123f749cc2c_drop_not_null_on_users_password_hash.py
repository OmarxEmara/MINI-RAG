from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6123f749cc2c"             # keep what Alembic generated
down_revision = "84092dbfa70e"    # your current head
branch_labels = None
depends_on = None

def upgrade():
    # Explicitly drop the NOT NULL constraint
    op.execute("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;")

def downgrade():
    # Restore NOT NULL if you ever roll back
    op.execute("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;")
