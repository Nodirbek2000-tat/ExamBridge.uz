#!/bin/sh
set -e

echo "⏳ Waiting for PostgreSQL..."
until python -c "
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection
connection.ensure_connection()
print('ok')
" 2>/dev/null | grep -q ok; do
  echo "  DB not ready, retrying in 2s..."
  sleep 2
done
echo "✅ PostgreSQL is ready"

echo "📦 Running migrations (data is preserved)..."
python manage.py migrate --noinput

echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "🚀 Starting app..."
exec "$@"
