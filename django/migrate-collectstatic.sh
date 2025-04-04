#!/bin/bash

# Wait for the database to be ready
echo "Waiting for database..."
{ python manage.py wait_for_db || { echo "Error: Wait for database failed"; exit 1; } }

# Run migrations
echo "Running migrations..."
{ python manage.py migrate --noinput || { echo "Error: Migrate failed"; exit 1; } }

# Collect static files
echo "Collecting static files..."
{ python manage.py collectstatic --noinput --clear || { echo "Error: Collect static files failed"; exit 1; } }

# Load localizations
echo "Loading localizations..."
{ python manage.py load_app_localization || { echo "Error: Loading localizations failed"; exit 1; } }
