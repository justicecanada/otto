#!/bin/sh

echo "Running database migrations..."
python manage.py migrate || { echo "Error: Migrate failed"; exit 1; }

echo "Resetting app data..."
python manage.py reset_app_data apps terms groups library_mini security_labels || { echo "Error: Reset app data failed"; exit 1; }

echo "Loading corporate library..."
python manage.py load_corporate_library --force || { echo "Error: Load corporate library failed"; exit 1; }

echo "Loading laws XML..."
python manage.py load_laws_xml --download --reset --small || { echo "Error: Load laws XML failed"; exit 1; }

echo "Cleaning static files..."
rm -r staticfiles/* || { echo "Error: Cleaning static files failed"; exit 1; }

echo "Collecting static files..."
python manage.py collectstatic --noinput || { echo "Error: Collect static files failed"; exit 1; }

echo "Setup completed successfully!"
