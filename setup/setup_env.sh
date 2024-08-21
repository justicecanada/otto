#!/bin/bash

ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"

# Check if .env.example file exists
if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
    echo "Error: .env.example file not found."
    exit 1
fi

# If .env doesn't exist, copy it from .env.example
if [ ! -f "$ENV_FILE" ]; then
    echo ".env file not found. Creating one from .env.example."
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    echo ".env file created. Please review and edit if necessary."
fi

while true; do
    # Display .env contents
    echo "Current .env file contents:"
    echo "----------------------------"
    cat "$ENV_FILE"
    echo "----------------------------"

    # Ask user if values are correct
    read -p "Are all the values correct? (y/N): " confirm

    if [[ $confirm =~ ^[Yy]$ ]]; then
        echo "Proceeding with current .env values."
        break
    else
        nano "$ENV_FILE"
    fi
done

# Load the environment variables from file
source .env

# Check if the user is logged in or not. If not, perform the az login.
if ! az account show; then
    az login
fi

# Set environment variables
export TENANT_ID=$(az account show --query tenantId --output tsv)
export SUBSCRIPTION_NAME=$(az account show --query name --output tsv)
export ENTRA_CLIENT_ID=$(az ad app list --display-name "${ENTRA_CLIENT_NAME}" --query "[].{appId:appId}" --output tsv)
export ENTRA_AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"

# Export dynamically generated variables
export RESOURCE_GROUP_NAME="${APP_NAME}${INTENDED_USE^^}Rg"
export KEYVAULT_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-kv"
export COGNITIVE_SERVICES_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-cs"
export OPENAI_SERVICE_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-openai"
export AKS_CLUSTER_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-aks"
export DISK_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-disk"
export STORAGE_NAME="jus${INTENDED_USE,,}${APP_NAME,,}storage"
export ACR_NAME="jus${INTENDED_USE,,}${APP_NAME,,}acr"
export DJANGODB_RESOURCE_NAME="jus-${INTENDED_USE,,}-${APP_NAME,,}-db"
export HOST_NAME="${HOST_NAME_PREFIX}.canadacentral.cloudapp.azure.com"

# TODO: Replace "OttoA" with ${APP_NAME} below. This is only temporary to help the Cloud Team avoid their cost management issues.

# Create terraform/.tfvars file
cat > terraform/.tfvars <<EOF
app_name = "OttoA"
environment = "${ENVIRONMENT}"
location = "${LOCATION}"
classification = "${CLASSIFICATION}"
cost_center = "${COST_CENTER}"
criticality = "${CRITICALITY}"
owner = "${OWNER}"
group_name = "${GROUP_NAME}"
resource_group_name = "${RESOURCE_GROUP_NAME}"
keyvault_name = "${KEYVAULT_NAME}"
cognitive_services_name = "${COGNITIVE_SERVICES_NAME}"
openai_service_name = "${OPENAI_SERVICE_NAME}"
aks_cluster_name = "${AKS_CLUSTER_NAME}"
disk_name = "${DISK_NAME}"
storage_name = "${STORAGE_NAME}"
acr_name = "${ACR_NAME}"
djangodb_resource_name = "${DJANGODB_RESOURCE_NAME}"
host_name_prefix = "${HOST_NAME_PREFIX}"
EOF
