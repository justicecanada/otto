#!/bin/sh

# Wait for the database to be ready
echo "Waiting for database..."
{ python manage.py wait_for_db || { echo "Error: Wait for database failed"; exit 1; } }

# Run migrations
echo "Running migrations..."
{ python manage.py migrate --noinput || { echo "Error: Migrate failed"; exit 1; } }

# Collect static files
echo "Collecting static files..."
{ python manage.py collectstatic --noinput --clear || { echo "Error: Collect static files failed"; exit 1; } }

# Reset app data
echo "Resetting app data..."
{ python manage.py reset_app_data apps terms groups library_mini security_labels cost_types || { echo "Error: Reset app data failed"; exit 1; } }

# Load initial data
echo "Loading corporate library..."
{ python manage.py load_corporate_library --force || { echo "Error: Load corporate library failed"; exit 1; } }

# Load laws XML
echo "Loading laws XML..."
{ python manage.py load_laws_xml --reset --small || { echo "Error: Load laws XML failed"; exit 1; } }

# Load users
echo "Syncing users..."
{ python manage.py sync_users || { echo "Error: Sync users failed"; exit 1; } }

# Check if OTTO_ADMIN is provided
if [ -n "$OTTO_ADMIN" ]; then
    echo "Setting Otto admin(s)..."
    
    # Use xargs to trim whitespace and run the command for each admin
    echo "$OTTO_ADMIN" | tr ',' '\n' | while read -r admin; do
        if [ -n "$admin" ]; then
            echo "Setting $admin as Otto admin..."
            python manage.py set_admin_user "$admin"
        fi
    done
fi

echo "Initial setup completed successfully!"
