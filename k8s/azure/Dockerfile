FROM python:3.11-alpine

# Install necessary dependencies
RUN apk add --no-cache \
    curl \
    bash \
    ca-certificates \
    gnupg \
    unzip \
    openssl \
    python3 \
    py3-pip \
    gcc \
    python3-dev \
    musl-dev \
    linux-headers

# Install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/

# Install Helm
RUN wget https://get.helm.sh/helm-v3.12.0-linux-amd64.tar.gz && \
    tar -zxvf helm-v3.12.0-linux-amd64.tar.gz && \
    mv linux-amd64/helm /usr/local/bin/helm && \
    rm -rf helm-v3.12.0-linux-amd64.tar.gz linux-amd64

# Install Azure CLI
RUN pip3 install --upgrade pip && \
    pip3 install azure-cli

# Install Terraform
RUN wget https://releases.hashicorp.com/terraform/1.5.0/terraform_1.5.0_linux_amd64.zip && \
    unzip terraform_1.5.0_linux_amd64.zip && \
    mv terraform /usr/local/bin/ && \
    rm terraform_1.5.0_linux_amd64.zip

# Clean up
RUN apk del gcc python3-dev musl-dev linux-headers && \
    rm -rf /var/cache/apk/*

# Set working directory
WORKDIR /scripts

# Default command
CMD ["/bin/bash"]
