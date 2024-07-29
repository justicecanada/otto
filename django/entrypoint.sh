#!/bin/sh
# python manage.py migrate

# We may not actually want to run then following every time the container is created...
# bash .devcontainer/post-create.sh

exec "$@"
