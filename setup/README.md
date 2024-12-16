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

- **Registration of Required Resource Providers:** Ensure that the following resource providers are registered in your Azure subscription:
  - Microsoft.Storage
  - Microsoft.KeyVault
  - Microsoft.ContainerRegistry
  - Microsoft.CognitiveServices
  - Microsoft.ContainerService
  - Microsoft.DBforPostgreSQL
  - Microsoft.Network
  - Microsoft.Compute
  - Microsoft.OperationalInsights
  - Microsoft.ManagedIdentity

  To register these providers:
  - In the [Azure Portal](https://portal.azure.com), search for "Resource providers" in the top search bar.
  - Select "Resource providers" from the search results.
  - In the list of resource providers, find each of the providers listed above.
  - If the status of any provider is not "Registered", select it and click the "Register" button at the top of the page.
  - Wait for the registration process to complete for each provider.

- **Agreement to Responsible AI Terms:** Follow these steps to accept the Responsible AI terms:
  - Log in to the [Azure Portal](https://portal.azure.com).
  - Navigate to "Create a resource."
  - Search for and select a Cognitive Service (e.g., Language Service).
  - Click "Create."
  - Review and accept the Responsible AI terms when prompted.
  - Cancel the resource creation after accepting the terms. (Terraform will create the necessary resources.)

- **Azure OpenAI Content Filter Modifications:** 
  - This step is required only once per subscription.
  - Visit [this link](https://customervoice.microsoft.com/Pages/ResponsePage.aspx?id=v4j5cvGGr0GRqy180BHbR7en2Ais5pxKtso_Pz4b1_xUMlBQNkZMR0lFRldORTdVQzQ0TEI5Q1ExOSQlQCN0PWcu) to request an exemption from the default content filtering. (Note: Do not use aka.ms/oai/rai/exceptions as that will be route the request through US government channels.)
  - Fill out the form to apply for modified content filters. This is necessary because the organization's use case involves processing information where standard content filtering is not appropriate.
  - Wait for Microsoft's approval before proceeding with the deployment.

- **Azure OpenAI Embedding Quota Increase Request:**  
  - This step is required only once per Azure subscription.
  - To request a quota increase, visit [this link](https://aka.ms/aoai/quotaincrease).
  - Select **"Standard"** as the deployment type.
  - In the justification section, include the following details:
    - Specify that the request is for the model **"text-embedding-3-large"**.
    - Request an increase to **700K TPM (tokens per minute)**.
    - Explain that the request is due to rate-limiting issues given the volume of text ingestion required for departmental use cases as part of the application's operational needs.
  - Once the quota increase is approved, update the .env file with the new quota limit so that Terraform will continue to manage it.
  - **Note**: A quota increase is typically approved only if the current quota limits are being reached so it might be necessary to submit the request after the initial deployment.

- **Agreement to Disable Abuse Monitoring:** 
  - This step is required only once per subscription.
  - Visit [this link](https://ncv.microsoft.com/3a140V2W0l) to request an exemption from the default abuse monitoring.
  - Fill out the form to apply disable abuse monitoring. This is necessary because the organization has data residency requirements and the storage of prompts and completions must remain in Canada. SA-9(5)
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

- **Increase Resource Quota Limits**
  - To allow proper scaling of the infrastructure, increase the default vCPU quota in Azure:
    - Log in to the [Azure Portal](https://portal.azure.com).
    - Search for and select "Quotas" in the top search bar.
    - On the Quotas page, select "Compute" from the provider dropdown.
    - Find and select "Total Regional vCPUs" for the deployment region.
    - Click "New quota request" at the top of the page.
    - Choose "Enter a new limit" and set the new value:
      - For DEV environment: 16 vCPUs
      - For UAT and PROD environments: 64 vCPUs or higher, based on the scaling needs
    - Submit the request.
  - Important Notes:
    - Quota increase requests typically process within hours but may take up to 2 business days.
    - Be prepared to justify larger quota increases if automatic approval is not granted.
    - Increasing quotas doesn't incur costs; you're only charged for resources you use.
    - Monitor resource usage and adjust quotas as needed to ensure proper application scaling.

- **Child DNS Zone Creation and Permissions:**

  Ensure that a child DNS zone is created under the parent zone (`cloud.justice.gc.ca`) with a name matching the value of the **DOMAIN_NAME** variable in the corresponding environment file. Additionally, ensure that the required managed identities have permissions to manage DNS records in the child zone.

  - **Create the Child DNS Zone:**

    1. Log in to the [Azure Portal](https://portal.azure.com).
    2. Navigate to the parent DNS zone: **`cloud.justice.gc.ca`**.
    3. On the **Overview** page, click **"+ Child zone"**.
    4. Provide the following details:
      - **Name**: Set this to the value of the **DOMAIN_NAME** variable from the environment file for the specific deployment (e.g., `otto-dev`, `otto-uat`, `otto-preprod`, or `otto` for production).
      - **Resource group**: Select the same resource group as the parent zone.
    5. Click **"Review + create"** to finalize.

  - **Grant Permissions to Managed Identities:**

    1. After creating the child zone, go to the **Access Control (IAM)** section of the child zone.
    2. Click **"+ Add" > "Add role assignment"**.
    3. Assign the **DNS Zone Contributor** role to the following managed identities:
      - `jumpbox-identity` for the respective environment.
      - `otto-identity` for the respective environment.
    4. Save the changes.

    > **Important**: This step must be completed **after** the Terraform deployment (which creates the managed identities) but **before** the AKS cluster deployment. The **DOMAIN_NAME** variable must match the child DNS zone name exactly to ensure proper DNS configuration.

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

Run the following command to create the infrastructure for the specified environment:

```bash
bash run_terraform.sh
```

> [!NOTE]
> You'll be prompted to either input the `ENTRA-CLIENT-SECRET` or use the value if it exists in the Key Vault already. Once the plan is generated, you'll be prompted to apply the changes. Enter `yes` to proceed with the deployment.


## DNS Zone Configuration Steps for Cloud Administrator

**Create Child DNS Zone**
1. Navigate to the parent DNS zone (cloud.justice.gc.ca) in Azure Portal
2. On the Overview page, click "+ Child zone"
3. Fill in the details:
   - Name: <environment>.cloud.justice.gc.ca
   - Resource group: Same as parent zone
4. Click "Review + create"

**Grant DNS Permissions**
1. In the newly created child zone
2. Select "Access Control (IAM)"
3. Click "+ Add" > "Add role assignment"
4. Select "DNS Zone Contributor" role
5. Assign to:
   - `jumpbox-identity` in the appropriate environment
   - `jus-<environment>-aks-identity` in the appropriate environment

These steps must be completed after the Terraform deployment (which creates the managed identities) but before the AKS cluster deployment.
      

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


## Appendix: Running Scripts with Parameters

To run terraform and pass parameters:

```bash
bash run_terraform.sh --env-file .env.dev --subscription OttoDev --skip-confirm y --auto-approve y --enable-debug n
```

To run k8s and pass parameters:

```bash
# Other options for cert-choice: skip, create, import
bash run_k8s.sh --env-file .env.dev --subscription OttoDev --cert-choice skip --init-script n --set-dns-label n
```

To run build_and_push and pass parameters:

```bash
./build_and_push_image.ps1 -subscription OttoDev -acr jusdevottoacr -skipNetworkCheck y
```
