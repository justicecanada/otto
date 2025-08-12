FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm
ENV PYTHONUNBUFFERED 1

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies and add PostgreSQL repository
RUN apt-get update \
    && apt-get install -y curl ca-certificates lsb-release \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc

RUN echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update

# Install ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg

# Install additional packages
RUN apt-get install -y --no-install-recommends gettext postgresql-client poppler-utils pgbouncer \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/archives/*

COPY django/requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev.txt \
    && rm -rf /tmp/pip-tmp
