python manage.py migrate
python manage.py reset_app_data apps terms groups library_mini security_labels
python manage.py load_corporate_library --force
python manage.py load_laws_xml --download --reset --small
rm -r staticfiles/*
python manage.py collectstatic --noinput
