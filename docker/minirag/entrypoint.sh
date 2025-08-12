#!/usr/bin/env bash
set -euo pipefail

# Make /app imports visible (models, controllers, main, etc.)
export PYTHONPATH="/app:${PYTHONPATH:-}"

echo "Running database migrations..."
alembic -c /app/models/db_schemes/minirag/alembic.ini upgrade head

echo "Starting API..."
cd /app
exec uvicorn main:app --host 0.0.0.0 --port 8000
