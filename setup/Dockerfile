FROM mcr.microsoft.com/azure-cli:cbl-mariner2.0

RUN tdnf install -y gettext tar gawk nano && tdnf clean all

RUN az aks install-cli

# Install Terraform
RUN curl -Lo terraform.zip https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip && \
    mkdir -p /usr/local/bin && \
    gunzip -c terraform.zip > /usr/local/bin/terraform && \
    chmod +x /usr/local/bin/terraform && \
    rm terraform.zip

# Set working directory
WORKDIR /workspace

# Default command
CMD ["/bin/bash"]