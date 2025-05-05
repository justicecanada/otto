# ============================================================================
# Otto AKS Python/Django Image - GoC SSL Trust Chain & Security Rationale
# ============================================================================
# Why these choices?
# - Python 3.11-slim-bookworm: Python 3.13+ enforces strict X.509 (AKI) checks
#   that break compatibility with GoC CA as of May 2025. Python 3.11 is secure,
#   widely supported, and works with the GoC trust chain. Bookworm (Debian 12)
#   is the latest stable base, with faster security updates than Bullseye.
# - System CA trust: We bake the GoC CA into the system trust at build time,
#   ensuring all Python/OpenSSL clients trust GoC endpoints without hacks.
# - Certifi override: We symlink Python’s certifi bundle to the system CA
#   bundle, so requests and other libraries inherit the correct trust chain.
# - No runtime CA mounts/initContainers: Simpler, more robust, and easier to
#   audit than dynamic CA injection or Key Vault CSI for this use case.
# - All other security and dependency practices are standard for AKS workloads.
# ============================================================================

FROM python:3.11-slim-bookworm

WORKDIR /django

# Environment variables for non-interactive install and tiktoken cache
ENV DEBIAN_FRONTEND=noninteractive \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache

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

# Download and install the GoC root CA (baked into system trust)
RUN wget -q --header="Host: pki-icp.canada.ca" \
    http://167.43.15.18/aia/GoC-GdC-Root-A.crt \
    -O /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
    chmod 644 /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt && \
    update-ca-certificates --fresh

# Install Python dependencies
# Vulnerability CVE-2024-6345 is fixed in setuptools 70.0.0
# and later. The vulnerability is present in all versions of
# setuptools prior to 70.0.0; however, the fix is not included in
# the Python 3.11-slim-bookworm image. This is a workaround to
# ensure that the latest version of setuptools is installed.
COPY requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check install --upgrade pip "setuptools>=70.0" wheel && \
    pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt && \
    rm -rf /tmp/pip-tmp

# Ensure Python uses the system CA bundle instead of certifi’s
RUN rm -f /usr/local/lib/python3.11/site-packages/certifi/cacert.pem && \
    ln -s /etc/ssl/certs/ca-certificates.crt /usr/local/lib/python3.11/site-packages/certifi/cacert.pem && \
    sed -i 's|return _certifi_where()|return "/etc/ssl/certs/ca-certificates.crt"|' /usr/local/lib/python3.11/site-packages/certifi/core.py

# Copy application code
COPY . /django

# Make scripts executable
RUN chmod +x /django/*.sh

# Create tiktoken cache directory and pre-cache tokens
RUN mkdir -p $TIKTOKEN_CACHE_DIR && \
    python /django/cache_tiktoken.py

# Run the localization script and remove the .env file if it exists
RUN python3 manage.py load_app_localization && \
    rm -f /django/.env

CMD ["daphne", "--bind", "0.0.0.0", "--port", "8000", "otto.asgi:application"]
