FROM python:3.11-slim-bullseye

WORKDIR /django

RUN apt-get update && apt-get install -y postgresql-client poppler-utils procps

COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev_celery.txt \
    && rm -rf /tmp/pip-tmp

COPY . /django

RUN chmod +x /django/*.sh

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
