#!/bin/bash

ENV_FILE=".env"
ENV_EXAMPLE_FILE=".env.example"

# Ensure Azure CLI is logged in
if ! az account show &>/dev/null; then
    echo "Not logged in to Azure. Please log in."
    az login
fi

# If SUBSCRIPTION_ID is already set, confirm if user wants to change it
if [ -n "$SUBSCRIPTION_ID" ]; then
    read -p "Subscription ID is already set. Do you want to change it? (y/N): " confirm
    if [[ $confirm =~ ^[Yy]$ ]]; then
        unset SUBSCRIPTION_ID
    else
        echo "Using current subscription: $SUBSCRIPTION_ID"
    fi
fi

# If SUBSCRIPTION_ID is not set, prompt user to select one
if [ -z "$SUBSCRIPTION_ID" ]; then

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

fi

# Check if .env.example file exists
if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
    echo "Error: $ENV_EXAMPLE_FILE file not found."
    exit 1
fi

# Function to extract version from file
get_version() {
    grep "^ENV_VERSION=" "$1" | cut -d '=' -f2 | cut -d '#' -f1 | tr -d ' '
}

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "$ENV_FILE file not found. Creating from $ENV_EXAMPLE_FILE..."
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    echo "$ENV_FILE file created successfully."
    exit 0
fi

# Get versions
example_version=$(get_version "$ENV_EXAMPLE_FILE")
current_version=$(get_version "$ENV_FILE")

# Compare versions
if [ "$(printf '%s\n' "$current_version" "$example_version" | sort -V | tail -n1)" != "$current_version" ]; then
    echo "Your $ENV_FILE file (version $current_version) is outdated. The latest version is $example_version."
    read -p "Do you want to update your $ENV_FILE file? (y/n): " answer

    if [[ $answer =~ ^[Yy]$ ]]; then
        # Create backup
        backup_file="$ENV_FILE.bk_$(date +%Y%m%d_%H%M%S)"
        cp "$ENV_FILE" "$backup_file"
        echo "Backup created: $backup_file"

        # Create new .env file
        cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
        echo "$ENV_FILE file has been updated to version $example_version."
        echo "Please review the new $ENV_FILE file and adjust any custom settings as needed."
    else
        echo "Update cancelled. Your $ENV_FILE file remains unchanged."
    fi
else
    echo "Your $ENV_FILE file is up to date (version $current_version)."
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

# Set the environment variables
export TENANT_ID=$(az account show --query tenantId --output tsv)
export ENTRA_CLIENT_ID=$(az ad app list --display-name "${ENTRA_CLIENT_NAME}" --query "[].{appId:appId}" --output tsv)
export ENTRA_AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"

# Set the dynamically generated variables
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
host_name_prefix = "${HOST_NAME_PREFIX}"
gpt_35_turbo_capacity = ${GPT_35_TURBO_CAPACITY}
gpt_4_turbo_capacity = ${GPT_4_TURBO_CAPACITY}
gpt_4o_capacity = ${GPT_4o_CAPACITY}
text_embedding_3_large_capacity = ${TEXT_EMBEDDING_3_LARGE_CAPACITY}
EOF
