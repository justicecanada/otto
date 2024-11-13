FROM python:3.11-slim-bullseye

WORKDIR /django

RUN apt-get update && apt-get install -y postgresql-client poppler-utils

COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev_celery.txt \
    && rm -rf /tmp/pip-tmp

COPY . /django

RUN chmod 0755 /django/entrypoint.sh

ENTRYPOINT ["/django/entrypoint.sh"]
