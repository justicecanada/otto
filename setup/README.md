# Otto Setup Guide

This guide will walk you through the process of setting up and deploying the Otto infrastructure using Docker and Terraform.

## Prerequisites

Before you start setting up and deploying the Otto infrastructure, ensure you have the following prerequisites:

- **Docker:** Ensure Docker is installed and running on your local machine. Docker is needed to build and run the container for the setup process.  You can download it from [Docker's official website](https://docs.docker.com/compose/install/).
- **Git:** Install Git to clone the Otto repository. You can download it from [Git's official website](https://git-scm.com/downloads).
- **VS Code:** Install Visual Studio Code if you are unable to login to Azure using `--use-device-code`. You can download it from [Visual Studio Code's official website](https://code.visualstudio.com/).
- **Azure OpenAI approval:** Ensure that Azure OpenAI has been approved by Microsoft for deployment on your target subscription. This may involve obtaining the necessary permissions and completing any required approval processes with Microsoft.
- **Azure OpenAI quota:** Verify that there is sufficient quota assigned to your subscription for Azure OpenAI services. This includes checking that you have enough capacity to handle the expected workload and usage.
- **Entra Callback URL:** Ensure that you have the callback URL for the Entra ID service to match the target. This is required to access the deployment of the AKS cluster once it is deployed.

## Prepare for Deployment

### 1. Clone the repo and navigate to the `setup` folder

```cmd
git clone https://github.com/justice-bac/otto.git
cd otto
git checkout cloud
cd setup
```

### 2. Configure the environment variables

Copy the `.env.example` file to `.env` and modify the values as needed for your specific deployment:

```cmd
copy .env.example .env
```

### 3. Build and connect to the setup container

The setup container contains all the necessary tools and dependencies to deploy the Otto infrastructure. You can choose to open the container in Visual Studio Code or directly in the Docker terminal.

**Option 1**: Open in Visual Studio Code

Open Visual Studio Code and follow the steps below:

- Open the Command Palette (Ctrl+Shift+P)
- Type "Dev Containers: Open Folder in Container" and select the option
- Select the repo's `setup` folder

This process may take a few minutes the first time as it builds the container image. Once it completes, open a new terminal in Visual Studio Code to run the commands.

**Option 2**: Open in Docker

If you are able to login to Azure from within a container using `--use-device-code`, you can run the following command:

```bash
docker-compose up -d
docker exec -it otto-setup-container /bin/bash
```

## Create Infrastructure

### 1. Create infrastructure using Terraform

Review and apply any necessary infrastructure changes.

```bash
bash deploy_terraform.sh
```
**Note:** The `CLIENT_SECRET` is not stored in the `.tfvars` file for security reasons. You can provide it interactively when prompted.

### 2. Configure the OpenAI Capacity

After the Terraform script has completed deploying the OpenAI resource and its default model deployments, the capacity for each model deployment needs to be adjusted manually. This is necessary to ensure that the deployments have sufficient capacity to handle the expected workload.

**Note:** Instances of OpenAI within the same subscription share the same capacity pool. Ensure that the total capacity across all deployments does not exceed the subscription's capacity limit.

## Deploy the AKS Cluster

### Prerequisites

The image for Otto needs to exist in the container registry. If the Terraform script just created it, the image will not exist on the registry yet. The development team will need to push the latest image to the registry first before continuing.

### 1. Deploy the AKS cluster

Run the following command to deploy the AKS cluster:

```bash
bash deploy_k8s.sh
```
