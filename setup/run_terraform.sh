#!/bin/bash

# Function to clean up temporary files
cleanup() {
    rm -f backend_config.hcl
    unset ENTRA_CLIENT_SECRET
}
trap cleanup EXIT

# Source setup_env.sh to set environment variables and create .tfvars
source setup_env.sh

# Function to ensure Terraform state storage exists
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

# Function to check if a secret exists in the Key Vault and prompt to use it
check_and_get_secret() {
    local vault_name="$1"
    local secret_name="$2"
    local secret_value

    if secret_value=$(az keyvault secret show --vault-name "$vault_name" --name "$secret_name" --query value -o tsv 2>/dev/null); then
        read -p "Secret '$secret_name' exists in Key Vault '$vault_name'. Use it? (y/N): " use_secret
        if [[ $use_secret =~ ^[Yy]$ ]]; then
            echo "$secret_value"
        fi
    fi
}

# Check if the Entra client secret exists in the Key Vault
export ENTRA_CLIENT_SECRET=$(check_and_get_secret "$KEYVAULT_NAME" "ENTRA-CLIENT-SECRET")


# Change to the Terraform directory
cd terraform

# Create a temporary backend configuration file
cat > backend_config.hcl << EOF
resource_group_name  = "$TF_STATE_RESOURCE_GROUP"
storage_account_name = "$TF_STATE_STORAGE_ACCOUNT"
container_name       = "$TF_STATE_CONTAINER"
key                  = "$TF_STATE_KEY"
EOF

# Ensure terraform is initialized and upgraded
terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

# Apply the Terraform configuration
if [ -n "$ENTRA_CLIENT_SECRET" ]; then
    terraform apply -var-file=.tfvars -var="entra_client_secret=$ENTRA_CLIENT_SECRET"
else
    terraform apply -var-file=.tfvars
fi
