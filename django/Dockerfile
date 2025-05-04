FROM python:3.11-slim-bookworm

WORKDIR /django

# Set environment variables
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies and clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gettext \
    postgresql-client \
    poppler-utils \
    procps \
    wget \
    ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Install Government of Canada Root CA
RUN wget -q --header="Host: pki-icp.canada.ca" \
    http://167.43.15.18/aia/GoC-GdC-Root-A.crt \
    -O /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
    chmod 644 /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
    update-ca-certificates --fresh

# Configure Python SSL trust store
RUN rm -f $(python -c "import certifi; print(certifi.where())") && \
    ln -s /etc/ssl/certs/ca-certificates.crt $(python -c "import certifi; print(certifi.where())") && \
    sed -i "s|return _certifi_where()|return '/etc/ssl/certs/ca-certificates.crt'|" \
    $(python -c "import certifi; print(certifi.__file__)")

# Align OpenSSL configuration with system CA bundle
RUN rm -f /usr/lib/ssl/cert.pem && \
    ln -s /etc/ssl/certs/ca-certificates.crt /usr/lib/ssl/cert.pem

# Install Python dependencies
COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check install --upgrade pip setuptools wheel && \
    pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt && \
    rm -rf /tmp/pip-tmp

# Copy application code
COPY . /django

# Final setup
RUN chmod +x /django/*.sh && \
    mkdir -p $TIKTOKEN_CACHE_DIR && \
    python /django/cache_tiktoken.py

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
