#!/bin/sh

echo "Running database migrations..."
{ python manage.py migrate || { echo "Error: Migrate failed"; exit 1; } }

echo "Resetting app data..."
{ python manage.py reset_app_data apps terms groups library_mini security_labels cost_types || { echo "Error: Reset app data failed"; exit 1; } }

echo "Loading corporate library..."
{ python manage.py load_corporate_library || { echo "Error: Load corporate library failed"; exit 1; } }

echo "Loading laws XML..."
{ python manage.py load_laws_xml --reset --small || { echo "Error: Load laws XML failed"; exit 1; } }

echo "Cleaning static files..."
if [ -d "staticfiles" ]; then
    rm -rf staticfiles/*
    echo "Static files cleaned successfully."
else
    echo "staticfiles directory does not exist. Skipping cleaning."
fi

echo "Collecting static files..."
{ python manage.py collectstatic --noinput || { echo "Error: Collect static files failed"; exit 1; } }

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

echo "Setup completed successfully!"
