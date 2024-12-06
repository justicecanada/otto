FROM python:3.11-slim-bullseye

WORKDIR /django

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install additional packages
RUN apt-get install -y --no-install-recommends gettext postgresql-client poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/archives/*

COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev_celery.txt \
    && rm -rf /tmp/pip-tmp

COPY . /django

RUN chmod 0755 /django/migrate-collectstatic.sh

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
