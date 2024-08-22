# Otto Setup Guide for Cloud Admins

This guide outlines the process for deploying Otto infrastructure using Azure Cloud Shell.

## Prerequisites

Before proceeding with the Otto infrastructure deployment using Azure Cloud Shell, ensure you have the following:

- **Azure Subscription with Necessary Permissions:** An active Azure subscription is required, along with sufficient permissions to create resources. You should have at least the **Contributor** or **Owner** role at the subscription level to manage resource groups and deploy services.

- **Entra Callback URL Configured for AKS Cluster Access:** You need to have the callback URL for the Entra ID service configured correctly. This URL is essential for accessing the AKS cluster once it is deployed, allowing secure communication and authentication for your applications.

For more information on Azure roles and permissions, refer to the [Azure RBAC documentation](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview).

**Note:** For information on Azure OpenAI content filter modifications, see the [Registration for modified content filters and/or abuse monitoring](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/limited-access#registration-for-modified-content-filters-andor-abuse-monitoring) documentation. This step has already been completed for Justice Canada.

## Deployment Steps

### 1. Open Azure Cloud Shell and access the Otto repository:

You have two options:

**Option A: Clone the repository (if you haven't done so before)**

```bash
git clone https://github.com/justicecanada/otto.git
cd otto/setup
```

**Option B: Update an existing repository**

If you've previously cloned the Otto repository, navigate to the Otto directory and pull the latest changes:

```bash
cd otto
git pull
cd setup
```

### 2. Create infrastructure using Terraform:

```bash
bash run_terraform.sh
```

Note: You'll be prompted to either input the `ENTRA-CLIENT-SECRET` or use the value if it exists in the Key Vault already.

### 3. Deploy the AKS cluster:

```bash
bash run_k8s.sh
```

Important: Ensure the Otto image exists in the container registry before deploying the AKS cluster. (The image is built and pushed as part of the Continuous Integration pipeline.)

# Appendix: Development Team Guide

For developers who want to emulate and test the scripts within a Docker container:

## Prerequisites

- Docker installed on your local machine
- Git installed
- Visual Studio Code (recommended)

## Setup Process

### 1. Clone the repository and navigate to the setup folder:

```bash
git clone https://github.com/justicecanada/Otto.git
cd Otto/setup
```

### 2. Build and connect to the setup container:

Option 1: Using Visual Studio Code
- Open the Command Palette (Ctrl+Shift+P)
- Type "Dev Containers: Open Folder in Container" and select it
- Choose the repo's `setup` folder

Option 2: Using Docker CLI
```bash
docker-compose up -d
docker exec -it otto-setup-container /bin/bash
```

### 3. Run the deployment scripts:

Once inside the container, follow the same deployment steps as outlined in the Cloud Admin guide above.

Note: When testing locally, ensure you have the necessary Azure credentials and permissions configured within your development environment.
