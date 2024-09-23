#!/bin/bash

# Ensure Azure CLI is logged in
if ! az account show &>/dev/null; then
    echo "Not logged in to Azure. Please log in."
    az login
fi

# CM-9: Prompt user to select an environment
echo "Available environments:"
env_files=($(ls .env* 2>/dev/null | sort))

# Check if any .env files were found
if [ ${#env_files[@]} -eq 0 ]; then
    echo "No environment files found."
    exit 1
fi

for i in "${!env_files[@]}"; do 
    echo "$((i+1)). ${env_files[$i]}"
done

while true; do
    read -p "Select an environment (enter the number): " selection
    if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#env_files[@]}" ]; then
        ENV_FILE="${env_files[$((selection-1))]}"
        echo "Selected environment: $ENV_FILE"
        break
    else
        echo "Invalid selection. Please choose a number from the list above."
    fi
done

# List available subscriptions and prompt user to select one
echo "Available subscriptions:"
az account list --query "[].{SubscriptionId:id, Name:name}" --output table

while true; do
    read -p "Enter the Subscription ID you want to use: " SUBSCRIPTION_ID
    if az account show --subscription "$SUBSCRIPTION_ID" &>/dev/null; then
        az account set --subscription "$SUBSCRIPTION_ID"
        export SUBSCRIPTION_ID
        echo "Subscription set to: $SUBSCRIPTION_ID"
        break
    else
        echo "Invalid Subscription ID. Please try again."
    fi
done

# Display selected environment file contents
echo "Selected environment file contents:"
echo "----------------------------"
cat "$ENV_FILE"
echo "----------------------------"

# Ask user if values are correct
read -p "Are all the values correct? (y/N): " confirm

if [[ ! $confirm =~ ^[Yy]$ ]]; then
    # Ask the user if they want to open nano to edit the file
    read -p "Do you want to edit the $ENV_FILE file in nano? (y/N): " edit_confirm
    if [[ $edit_confirm =~ ^[Yy]$ ]]; then
        nano "$ENV_FILE"
    else
        echo "Please update the $ENV_FILE file with the correct values and run the script again."
        exit 1
    fi
fi

# Unset all environment variables
unset $(grep -v '^#' "$ENV_FILE" | sed -E 's/(.*)=.*/\1/' | xargs)
unset SITE_URL
unset DNS_LABEL

# Load the environment variables from file
source "$ENV_FILE"

echo "Environment variables loaded from $ENV_FILE"

# Validation and URL setting
if [ -n "$SITE_URL" ] && [ -n "$DNS_LABEL" ]; then
    echo "Error: Both SITE_URL and DNS_LABEL are set. Please choose only one option."
    return
elif [ -n "$DNS_LABEL" ]; then
    # Ensure LOCATION is set
    if [ -z "$LOCATION" ]; then
        echo "Error: LOCATION is not set. Please set the Azure region."
        return
    fi
    SITE_URL="https://${DNS_LABEL}.${LOCATION}.cloudapp.azure.com"
    echo "SITE_URL set to: $SITE_URL"
elif [ -z "$SITE_URL" ]; then
    echo "Error: Neither SITE_URL nor DNS_LABEL is set. Please set one of them."
    return
fi

# Extract HOST_NAME from SITE_URL
export DNS_LABEL
export SITE_URL
export HOST_NAME=${SITE_URL#https://}

export ENV_VERSION
export INTENDED_USE
export ADMIN_GROUP_NAMES
export ACR_PUBLISHERS_GROUP_NAMES
export ENTRA_CLIENT_NAME
export ORGANIZATION
export ALLOWED_IPS

export APP_NAME
export ENVIRONMENT
export LOCATION
export CLASSIFICATION
export COST_CENTER
export CRITICALITY
export OWNER
export DJANGO_ENV
export DJANGO_DEBUG
export OTTO_ADMIN
export ADMIN_EMAIL

export GPT_35_TURBO_CAPACITY
export GPT_4_TURBO_CAPACITY
export GPT_4o_CAPACITY
export TEXT_EMBEDDING_3_LARGE_CAPACITY

# Set the environment variables
export TENANT_ID=$(az account show --query tenantId --output tsv)
export ENTRA_CLIENT_ID=$(az ad app list --display-name "${ENTRA_CLIENT_NAME}" --query "[].{appId:appId}" --output tsv)
export ENTRA_AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"

# Set the dynamically generated variables
export RESOURCE_GROUP_NAME="${APP_NAME}${INTENDED_USE^^}Rg"
export KEYVAULT_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-kv"
export COGNITIVE_SERVICES_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-cs"
export OPENAI_SERVICE_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-openai"
export AKS_CLUSTER_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-aks"
export DISK_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-disk"
export STORAGE_NAME="${ORGANIZATION,,}${INTENDED_USE,,}${APP_NAME,,}storage"
export ACR_NAME="${ORGANIZATION,,}${INTENDED_USE,,}${APP_NAME,,}acr"
export DJANGODB_RESOURCE_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-db"
export TAGS="ApplicationName=${APP_NAME} Environment=${ENVIRONMENT} Location=${LOCATION} Classification=${CLASSIFICATION} CostCenter=\"${COST_CENTER}\" Criticality=${CRITICALITY} Owner=\"${OWNER}\""


# Set the Terraform state variables
export TF_STATE_RESOURCE_GROUP="TerraformStateRG"
export TF_STATE_STORAGE_ACCOUNT="tfstate${APP_NAME,,}${ENVIRONMENT,,}"
export TF_STATE_CONTAINER="tfstate"
export TF_STATE_KEY="${RESOURCE_GROUP_NAME}.tfstate"

# Create terraform/.tfvars file
cat > terraform/.tfvars <<EOF
app_name = "${APP_NAME}"
environment = "${ENVIRONMENT}"
location = "${LOCATION}"
classification = "${CLASSIFICATION}"
cost_center = "${COST_CENTER}"
criticality = "${CRITICALITY}"
owner = "${OWNER}"
admin_group_name = "${ADMIN_GROUP_NAME}"
acr_publishers_group_name = "${ACR_PUBLISHERS_GROUP_NAME}"
resource_group_name = "${RESOURCE_GROUP_NAME}"
keyvault_name = "${KEYVAULT_NAME}"
cognitive_services_name = "${COGNITIVE_SERVICES_NAME}"
openai_service_name = "${OPENAI_SERVICE_NAME}"
aks_cluster_name = "${AKS_CLUSTER_NAME}"
disk_name = "${DISK_NAME}"
storage_name = "${STORAGE_NAME}"
acr_name = "${ACR_NAME}"
djangodb_resource_name = "${DJANGODB_RESOURCE_NAME}"
gpt_35_turbo_capacity = ${GPT_35_TURBO_CAPACITY}
gpt_4_turbo_capacity = ${GPT_4_TURBO_CAPACITY}
gpt_4o_capacity = ${GPT_4o_CAPACITY}
text_embedding_3_large_capacity = ${TEXT_EMBEDDING_3_LARGE_CAPACITY}
admin_email = "${ADMIN_EMAIL}"
EOF
