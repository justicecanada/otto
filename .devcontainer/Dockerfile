FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# Disable Yarn APT repository (Yarn not required for this project).
# The base image adds the Yarn repo, but its GPG key is currently invalid,
# which breaks `apt-get update` during devcontainer builds.
RUN rm -f /etc/apt/sources.list.d/yarn.list /etc/apt/trusted.gpg.d/yarn.gpg /usr/share/keyrings/yarn.gpg || true

# Install GoC certificate FIRST before any other operations
RUN wget -q --header="Host: pki-icp.canada.ca" "http://167.43.15.18/aia/GoC-GdC-Root-A.crt" -O /tmp/GoC-GdC-Root-A.crt && \
    cp /tmp/GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/ && \
    update-ca-certificates && \
    rm /tmp/GoC-GdC-Root-A.crt

# Set SSL certificate environment variables for all subsequent operations
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Set Python unbuffered environment variable
ENV PYTHONUNBUFFERED=1

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install Node.js (includes npm) from the official upstream tarball so the
# repo's npm workspace for vendored browser assets works without pulling in the
# full Debian node/npm dependency tree.
ARG NODE_VERSION=22.22.3
RUN set -e; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) node_arch="x64" ;; \
        arm64) node_arch="arm64" ;; \
        *) echo "Unsupported Node.js architecture: $arch"; exit 1 ;; \
    esac; \
    cd /tmp; \
    node_dir="node-v${NODE_VERSION}-linux-${node_arch}"; \
    wget -q "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${node_dir}.tar.xz$" SHASUMS256.txt > SHASUMS256.node.txt; \
    wget -q "https://nodejs.org/dist/v${NODE_VERSION}/${node_dir}.tar.xz"; \
    sha256sum -c SHASUMS256.node.txt; \
    mkdir -p /usr/local/lib/nodejs; \
    tar -xJf "${node_dir}.tar.xz" -C /usr/local/lib/nodejs; \
    ln -sf "/usr/local/lib/nodejs/${node_dir}/bin/node" /usr/local/bin/node; \
    ln -sf "/usr/local/lib/nodejs/${node_dir}/bin/npm" /usr/local/bin/npm; \
    ln -sf "/usr/local/lib/nodejs/${node_dir}/bin/npx" /usr/local/bin/npx; \
    node --version; \
    npm --version; \
    rm -f /tmp/SHASUMS256.txt /tmp/SHASUMS256.node.txt "/tmp/${node_dir}.tar.xz"

## --- Base OS deps & PostgreSQL repo setup ---
# Add PostgreSQL APT repo (we will pin to PG 16 client to avoid 404s on rolling latest)
RUN set -e; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl lsb-release gnupg; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    # Create a pin so we don't accidentally pull a newer major (17/18) that may have transient mirror lag.
    printf "Package: postgresql-client* libpq*\nPin: release o=apt.postgresql.org\nPin-Priority: 500\n" > /etc/apt/preferences.d/pgdg.pref; \
    apt-get update;

# Install required packages including a specific PostgreSQL major client (16)
# We use explicit version wildcard to stay on latest minor of 16.* reducing 404 risk.
RUN set -e; \
    apt-get install -y --no-install-recommends \
    gettext \
    libreoffice-writer \
    postgresql-client-16 \
    libpq5 \
    bash sudo unzip \
    openssh-client \
    shellcheck \
    shfmt; \
    # Clean apt caches but keep list files until after tooling installs to reduce duplicate index downloads.
    apt-get clean; \
    rm -rf /var/cache/apt/archives/*

# Some networks/VPNs block outbound SSH on port 22.
# Configure OpenSSH client to use GitHub's SSH-over-HTTPS endpoint by default.
# (User-specific ~/.ssh/config can still override this.)
RUN set -e; \
    install -d /etc/ssh/ssh_config.d; \
    cat > /etc/ssh/ssh_config.d/otto-github-443.conf <<'EOF'
Host github.com
    Hostname ssh.github.com
    Port 443
    User git
EOF

COPY django/requirements*.txt /tmp/pip-tmp/
RUN pip3 --disable-pip-version-check --no-cache-dir install \
    -r /tmp/pip-tmp/requirements.txt -r /tmp/pip-tmp/requirements_dev.txt \
    && rm -rf /tmp/pip-tmp

# --- Azure CLI (required for fetching secrets) ---
# Add Azure CLI APT repo and install `az`.
RUN set -e; \
    apt-get update; \
    curl -sLS https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg; \
    echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/azure-cli/ $(lsb_release -cs) main" > /etc/apt/sources.list.d/azure-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends azure-cli; \
    apt-get clean; \
    rm -rf /var/cache/apt/archives/*

# --- GitHub CLI (for Copilot CLI / PR workflows) ---
RUN set -e; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor > /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    gh --version; \
    apt-get clean; \
    rm -rf /var/cache/apt/archives/*

# --- Terraform CLI (for formatting and validation) ---
ARG TF_VERSION=1.10.3
RUN set -e; \
    echo "Installing Terraform ${TF_VERSION}"; \
    curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" -o /tmp/terraform.zip; \
    unzip /tmp/terraform.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/terraform; \
    rm /tmp/terraform.zip; \
    terraform -version || true

# Clean up apt lists to reduce image size
RUN rm -rf /var/lib/apt/lists/*
