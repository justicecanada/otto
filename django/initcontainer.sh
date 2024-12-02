#!/bin/bash

# Set up pgBouncer based on environment variables
echo "Setting up pgBouncer."
envsubst < postgres_wrapper/pgbouncer_config/pgbouncer.ini.template > /etc/pgbouncer/pgbouncer.ini
envsubst < postgres_wrapper/pgbouncer_config/userlist.txt.template > /etc/pgbouncer/userlist.txt
# Restart pgBouncer
pgbouncer -R /etc/pgbouncer/pgbouncer.ini

# Wait for the database to be ready
echo "Waiting for database..."
{ python manage.py wait_for_db || { echo "Error: Wait for database failed"; exit 1; } }

# Run migrations
echo "Running migrations..."
{ python manage.py migrate --noinput || { echo "Error: Migrate failed"; exit 1; } }

# Collect static files
echo "Collecting static files..."
{ python manage.py collectstatic --noinput --clear || { echo "Error: Collect static files failed"; exit 1; } }
