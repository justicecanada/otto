FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm
ENV PYTHONUNBUFFERED 1

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg

COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt \
    && rm -rf /tmp/pip-tmp \