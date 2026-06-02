#!/bin/sh

# Start Celery worker to index corporate library
celery -A otto worker -l INFO --pool=gevent --concurrency=256 &
celery_pid=$!

# Migrate
echo "Applying migrations..."
{ python manage.py migrate || {
	echo "Error: Migrations failed"
	exit 1
}; }

# Reset app data
echo "Resetting app data..."
{ python manage.py reset_app_data groups library_mini security_labels cost_types presets cost_groups skills skill_tags || {
	echo "Error: Reset app data failed"
	exit 1
}; }

# Load initial data
echo "Loading corporate library..."
{ python manage.py load_corporate_library || {
	echo "Error: Load corporate library failed"
	exit 1
}; }

# Load laws XML
echo "Loading laws XML..."
{ python manage.py load_laws_xml --small --reset --start || {
	echo "Error: Load laws XML failed"
	exit 1
}; }

# Load users
echo "Syncing users..."
{ python manage.py sync_users || {
	echo "Error: Sync users failed"
	exit 1
}; }

# Check if DEFAULT_OTTO_ADMIN_USERS is provided
if [ -n "$DEFAULT_OTTO_ADMIN_USERS" ]; then
	echo "Setting Otto admin(s)..."

	# Use xargs to trim whitespace and run the command for each admin
	echo "$DEFAULT_OTTO_ADMIN_USERS" | tr ',' '\n' | while read -r admin; do
		if [ -n "$admin" ]; then
			echo "Setting $admin as Otto admin..."
			python manage.py set_admin_user "$admin"
		fi
	done
fi

# Load localizations
echo "Loading localizations..."
{ python manage.py load_app_localization || {
	echo "Error: Loading localizations failed"
	exit 1
}; }

# Kill Celery worker without leaving zombie
kill $celery_pid
wait $celery_pid

echo "Initial setup completed successfully!"
