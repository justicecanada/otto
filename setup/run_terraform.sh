#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Function to clean up temporary files
cleanup() {
    rm -f backend_config.hcl
    unset TF_VAR_entra_client_secret
}
trap cleanup EXIT

# Source setup_env.sh to set environment variables and create .tfvars
source setup_env.sh

# Check if the Entra client secret is stored in Key Vault
unset TF_VAR_entra_client_secret
if TF_VAR_entra_client_secret=$(az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "ENTRA-CLIENT-SECRET" --query value -o tsv 2>/dev/null); then
    read -p "Entra client secret exists in the Key Vault. Use it? (y/N): " use_secret
    if [[ $use_secret =~ ^[Yy]$ ]]; then
        # Set the Entra client secret as a Terraform variable
        export TF_VAR_entra_client_secret
    fi
fi

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



# Change to the Terraform directory
cd terraform

# Create a temporary backend configuration file
cat > backend_config.hcl << EOF
resource_group_name  = "$TF_STATE_RESOURCE_GROUP"
storage_account_name = "$TF_STATE_STORAGE_ACCOUNT"
container_name       = "$TF_STATE_CONTAINER"
key                  = "$TF_STATE_KEY"
EOF

# Ask if the user wants to auto-approve the Terraform plan
unset TF_CLI_ARGS_apply
read -p "Auto-approve the Terraform plan? (y/N): " auto_approve
if [[ $auto_approve =~ ^[Yy]$ ]]; then
    # Set the auto-approve flag
    export TF_CLI_ARGS_apply="-auto-approve"
fi

# Ask if the user wants to turn on debugging mode
unset TF_LOG
read -p "Enable Terraform debugging mode? (y/N): " enable_debug
if [[ $enable_debug =~ ^[Yy]$ ]]; then

    # Set the Terraform log level to debug
    export TF_LOG=DEBUG

    # Set the timestamp for debugging logs
    export TIMESTAMP=$(date +%Y%m%d%H%M%S)

    # Ensure terraform is initialized and upgraded
    terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure > debug-$TIMESTAMP-init.txt 2>&1

    # Apply the Terraform configuration
    terraform apply -var-file=.tfvars > debug-$TIMESTAMP-apply.txt 2>&1

else

    # Ensure terraform is initialized and upgraded
    terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

    # Apply the Terraform configuration
    terraform apply -var-file=.tfvars

fi
