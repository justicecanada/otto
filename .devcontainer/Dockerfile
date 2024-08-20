FROM mcr.microsoft.com/devcontainers/python:1-3.11-bullseye
ENV PYTHONUNBUFFERED 1

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends gettext postgresql-client poppler-utils \
    && apt-get clean && rm -rf /var/lib/apt/lists/* && rm -rf /var/cache/apt/archives/*

COPY django/requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev.txt \
    && rm -rf /tmp/pip-tmp
