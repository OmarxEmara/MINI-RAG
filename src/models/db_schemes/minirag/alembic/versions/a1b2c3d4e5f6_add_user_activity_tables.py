# migrations/versions/add_user_activity_tables.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9b82b81a72ac"  # Previous revision
branch_labels = None
depends_on = None

def upgrade():
    # Create chat_history table
    op.create_table(
        "chat_history",
        sa.Column("chat_id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, 
                 sa.ForeignKey("users.user_id", ondelete="CASCADE"), 
                 nullable=False),
        sa.Column("project_id", sa.Integer, 
                 sa.ForeignKey("projects.project_id", ondelete="CASCADE"), 
                 nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), 
                 nullable=False, server_default=sa.text("now()")),
    )
    
    # Create indexes for chat_history
    op.create_index("ix_chat_history_user_id", "chat_history", ["user_id"])
    op.create_index("ix_chat_history_project_id", "chat_history", ["project_id"])
    op.create_index("ix_chat_history_created_at", "chat_history", ["created_at"])
    
    # Create activity_type enum
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'activity_type') THEN
            CREATE TYPE activity_type AS ENUM (
                'LOGIN', 'LOGOUT', 'CHAT', 'UPLOAD', 'DELETE_FILE', 
                'PROCESS_FILES', 'SEARCH', 'INDEX_PROJECT', 'CREATE_PROJECT'
            );
        END IF;
    END$$;
    """)
    
    # Create user_activities table
    op.create_table(
        "user_activities",
        sa.Column("activity_id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, 
                 sa.ForeignKey("users.user_id", ondelete="CASCADE"), 
                 nullable=False),
        sa.Column("activity_type", 
                 postgresql.ENUM(name="activity_type", create_type=False), 
                 nullable=False),
        sa.Column("project_id", sa.Integer, 
                 sa.ForeignKey("projects.project_id", ondelete="SET NULL"), 
                 nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, 
                 server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), 
                 nullable=False, server_default=sa.text("now()")),
    )
    
    # Create indexes for user_activities
    op.create_index("ix_user_activities_user_id", "user_activities", ["user_id"])
    op.create_index("ix_user_activities_project_id", "user_activities", ["project_id"])
    op.create_index("ix_user_activities_created_at", "user_activities", ["created_at"])
    op.create_index("ix_user_activities_type", "user_activities", ["activity_type"])


def downgrade():
    # Drop indexes first
    op.drop_index("ix_user_activities_type", table_name="user_activities")
    op.drop_index("ix_user_activities_created_at", table_name="user_activities")
    op.drop_index("ix_user_activities_project_id", table_name="user_activities")
    op.drop_index("ix_user_activities_user_id", table_name="user_activities")
    
    op.drop_index("ix_chat_history_created_at", table_name="chat_history")
    op.drop_index("ix_chat_history_project_id", table_name="chat_history")
    op.drop_index("ix_chat_history_user_id", table_name="chat_history")
    
    # Drop tables
    op.drop_table("user_activities")
    op.drop_table("chat_history")
    
    # Drop enum type
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'activity_type') THEN DROP TYPE activity_type; END IF; END$$;")