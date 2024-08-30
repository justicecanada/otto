# Otto Setup Guide for Cloud Admins

This guide provides a comprehensive overview of deploying Otto infrastructure using Azure Cloud Shell. The deployment process requires specific prerequisites and configurations to ensure a successful setup.

## Prerequisites

Before deploying Otto infrastructure, ensure the following prerequisites are met:

- **Azure Subscription with Permissions:** You must have an active Azure subscription with sufficient permissions to create and manage resources. Ensure the following:
  - Log in to the [Azure Portal](https://portal.azure.com).
  - Navigate to "Subscriptions" in the left-hand menu.
  - Select your subscription and go to "Access control (IAM)."
  - Verify that your account has the required role. If not, contact your Azure administrator to grant you the necessary permissions.

- **Custom Domain Name (Optional):**
  - If you plan to use a custom domain, make sure you have access to the domain registrar to update the DNS records.

- **Registration of Cloud Shell:** Ensure that the **Microsoft.CloudShell** resource provider is registered in your Azure subscription.
  - In the [Azure Portal](https://portal.azure.com), search for "Resource providers" in the top search bar.
  - Select "Resource providers" from the search results.
  - In the list of resource providers, find "Microsoft.CloudShell".
  - If its status is not "Registered", select it and click the "Register" button at the top of the page.
  - Wait for the registration process to complete.

- **Agreement to Responsible AI Terms:** Follow these steps to accept the Responsible AI terms:
  - Log in to the [Azure Portal](https://portal.azure.com).
  - Navigate to "Create a resource."
  - Search for and select a Cognitive Service (e.g., Language Service).
  - Click "Create."
  - Review and accept the Responsible AI terms when prompted.
  - Cancel the resource creation after accepting the terms. (Terraform will create the necessary resources.)

- **Azure OpenAI Content Filter Modifications:** 
  - This step is required only once per subscription.
  - Visit [this link](https://aka.ms/oai/rai/exceptions) to request an exemption from the default content filtering and abuse monitoring.
  - Fill out the form to apply for modified content filters. This is necessary because the organization's use case involves processing data where standard content filtering is not appropriate.
  - Wait for Microsoft's approval before proceeding with the deployment.

- **Entra App Registration:**
  - Log in to the Azure Portal (https://portal.azure.com).
  - Navigate to "Microsoft Entra ID" and select "App registrations."
  - Click "New registration."
  - In the "Register an application" page:
    - Enter a descriptive name for your application.
    - Under "Supported account types," select "Accounts in this organizational directory only (Single tenant)."
  - In the "Redirect URI" section:
    - Select "Web" as the platform.
    - Add the following URIs, replacing placeholders with your specific values:
      - `<site-url>/accounts/login/callback` (if using a custom domain)
      - `https://<dns-label>.<location>.cloudapp.azure.com/accounts/login/callback` (if not using a custom domain)
      - `https://127.0.0.1/accounts/login/callback` (optional for local testing)
      - `http://localhost/accounts/login/callback` (optional for local testing)
  - Click "Register" to create the app registration and note the "Application (client) ID" from the overview page.
  - Go to "API permissions":
    - Click "Add a permission."
    - Select "Microsoft Graph" in the right panel.
    - Choose "Application permissions."
    - Add the following permissions:
      - `AuditLog.Read.All`
      - `Directory.Read.All`
      - `User.Read`
      - `User.Read.All`
    - Click "Add permissions" to save.
    - Grant admin consent for your organization.
  - Go to "Certificates & secrets":
     - Click "New client secret."
     - Enter a description for the secret.
     - Choose an expiration period.
     - Click "Add."
     - Copy and securely store the client secret value.
  - Use the Application (client) ID and client secret in your Terraform script or application configuration.

## Deployment Steps

### 1. Open Azure Cloud Shell and access the Otto repository:

Open an Azure Cloud Shell session and navigate to the `setup` directory within the Otto repository:

**If you have NOT YET cloned the repository:**

```bash
git clone https://github.com/justicecanada/otto.git
cd otto/setup
```

**If you've previously cloned the Otto repository, navigate to the Otto directory and pull the latest changes:**

```bash
cd otto
git pull
cd setup
```

### 2. Create infrastructure using Terraform:

Run the following command to create the infrastructure for the specified environment. Replace `<env>` with the environment you want to deploy to (e.g. `sandbox`, `uat`, `prod`).

```bash
bash run_terraform.sh <env>
```

> [!NOTE]
> You'll be prompted to either input the `ENTRA-CLIENT-SECRET` or use the value if it exists in the Key Vault already. Once the plan is generated, you'll be prompted to apply the changes. Enter `yes` to proceed with the deployment.

### 3. Deploy the AKS cluster:

If the container registry was created for the first time, the image will not exist in the registry. The Continuous Integration pipeline will need to be configured to build and push the image to the registry. (In the interim, the development team can manually push the image to the registry.)

```bash
bash run_k8s.sh
```

### 4. Configure the DNS records:

If you are using a custom domain, update the DNS records to point to the AKS cluster's public IP address. The script will output the public IP address to use for the DNS records.

# Appendix: Development Team Guide

To develop and test the scripts / terraform / kubernetes manifests, a VS Code devcontainer as well as a plain Docker image are provided. Ensure that you set the environment variables to a non-production test environment.

## Prerequisites

- Docker installed on your local machine
- Git installed
- Visual Studio Code (recommended)

## Setup Process

### 1. Clone the repository and navigate to the setup folder:

```bash
git clone https://github.com/justicecanada/otto.git
cd otto/setup
```

### 2. Build and connect to the setup container:

**If you are using Visual Studio Code:**
- Launch Visual Studio Code in a new window
- Open the Command Palette (Ctrl+Shift+P)
- Type "Dev Containers: Open Folder in Container" and select it
- Choose the repo's `setup` folder

**If you are using Docker CLI:**

```bash
docker-compose up -d
docker exec -it otto-setup-container /bin/bash
```

### 3. Run the deployment scripts:

Once inside the container, follow the same deployment steps as outlined in the Cloud Admin guide above.

> [!NOTE]
>  When testing locally, ensure you have the necessary Azure credentials and permissions configured within your development environment.
