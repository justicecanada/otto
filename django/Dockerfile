FROM python:3.11-slim-bullseye

WORKDIR /django

# Set environment variable for tiktoken cache
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install additional packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext postgresql-client poppler-utils procps wget ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/archives/*

# Download and install the GoC root certificate using wget
# TODO: Remove the header and replace the IP with the domain name when the host no longer resets the connection from the secondary IP
RUN wget -q --header="Host: pki-icp.canada.ca" http://167.43.15.18/aia/GoC-GdC-Root-A.crt -O /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt \
    && update-ca-certificates

COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt \
    && rm -rf /tmp/pip-tmp

COPY . /django

# Make scripts executable
RUN chmod +x /django/*.sh

# Create tiktoken cache directory
RUN mkdir -p $TIKTOKEN_CACHE_DIR

# Run the tiktoken caching script
RUN python /django/cache_tiktoken.py

# Compile the translation files
RUN python /django/manage.py load_app_localization

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
