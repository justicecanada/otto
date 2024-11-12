#!/bin/bash

if [ "$1" = "backup" ]; then

    # Backup the databases
    /django/backup_db.sh

else

    # Wait for the database to be ready
    echo "Waiting for database..."
    { python manage.py wait_for_db || { echo "Error: Wait for database failed"; exit 1; } }

    # Run migrations
    echo "Running migrations..."
    { python manage.py migrate --noinput || { echo "Error: Migrate failed"; exit 1; } }

    # Collect static files
    echo "Collecting static files..."
    { python manage.py collectstatic --noinput --clear || { echo "Error: Collect static files failed"; exit 1; } }

    # Start the application
    daphne --bind 0.0.0.0 --port 8000 otto.asgi:application
    
fi
