#!/bin/bash

# Function to clean up temporary files
cleanup() {
    rm -f backend_config.hcl
}
trap cleanup EXIT

# Source setup_env.sh to set environment variables and create .tfvars
source setup_env.sh

ensure_tf_state_storage() {
    echo "Ensuring Terraform state storage exists..."

    # Create or update resource group with tags
    if ! az group show --name "$TF_STATE_RESOURCE_GROUP" --only-show-errors &>/dev/null; then
        echo "Creating resource group: $TF_STATE_RESOURCE_GROUP"
        az group create --name "$TF_STATE_RESOURCE_GROUP" --location "$LOCATION" --tags $TAGS --only-show-errors
    fi

    # Create storage account if it doesn't exist
    if ! az storage account show --name "$TF_STATE_STORAGE_ACCOUNT" --resource-group "$TF_STATE_RESOURCE_GROUP" --only-show-errors &>/dev/null; then
        echo "Creating storage account: $TF_STATE_STORAGE_ACCOUNT"
        az storage account create --name "$TF_STATE_STORAGE_ACCOUNT" --resource-group "$TF_STATE_RESOURCE_GROUP" --location "$LOCATION" --sku Standard_LRS --tags $TAGS --only-show-errors
    fi

    # Create blob container if it doesn't exist
    if ! az storage container show --name "$TF_STATE_CONTAINER" --account-name "$TF_STATE_STORAGE_ACCOUNT" --auth-mode login --only-show-errors &>/dev/null; then
        echo "Creating blob container: $TF_STATE_CONTAINER"
        az storage container create --name "$TF_STATE_CONTAINER" --account-name "$TF_STATE_STORAGE_ACCOUNT" --auth-mode login --only-show-errors
    fi
}

# Create Terraform state storage
ensure_tf_state_storage

# Change to the Terraform directory
cd terraform

# Create a temporary backend configuration file
cp backend.hcl backend_config.hcl
sed -i "s/__RESOURCE_GROUP_NAME__/$TF_STATE_RESOURCE_GROUP/" backend_config.hcl
sed -i "s/__STORAGE_ACCOUNT_NAME__/$TF_STATE_STORAGE_ACCOUNT/" backend_config.hcl
sed -i "s/__CONTAINER_NAME__/$TF_STATE_CONTAINER/" backend_config.hcl
sed -i "s/__KEY__/$TF_STATE_KEY/" backend_config.hcl


# Ensure terraform is initialized and upgraded
terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

# Apply the Terraform configuration
terraform apply -var-file=.tfvars
