FROM python:3.13-slim-bookworm

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

# Copy GoC Root CA before Python certificate configuration
COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/
RUN chmod 644 /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt \
    && update-ca-certificates

COPY requirements*.txt /tmp/pip-tmp/

RUN pip3 --disable-pip-version-check install --upgrade pip setuptools wheel

RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt \
    && rm -rf /tmp/pip-tmp

# Ensure Python uses the system CA bundle instead of certifi’s,
# so that any custom or organization CA certificates (like the GoC root CA)
# injected at runtime are trusted by all Python HTTPS clients.
RUN rm -f /usr/local/lib/python3.13/site-packages/certifi/cacert.pem && \
    ln -s /etc/ssl/certs/ca-certificates.crt /usr/local/lib/python3.13/site-packages/certifi/cacert.pem && \
    sed -i 's/return _certifi_where()/return "\/etc\/ssl\/certs\/ca-certificates.crt"/' /usr/local/lib/python3.13/site-packages/certifi/core.py

COPY . /django

# Make scripts executable
RUN chmod +x /django/*.sh

# Create tiktoken cache directory
RUN mkdir -p $TIKTOKEN_CACHE_DIR

# Run the tiktoken caching script
RUN python /django/cache_tiktoken.py

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
