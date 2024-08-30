#!/bin/bash

# Function to clean up temporary files
cleanup() {
    rm -f backend_config.hcl
    unset TF_VAR_entra_client_secret
}
trap cleanup EXIT

# Function to display usage information
usage() {
    echo -e "\n\e[1;33mUsage:\e[0m"
    echo -e "\e[1;32m$0 <env>\e[0m"
    echo
    echo -e "\e[1;33mWhere <env> must be one of:\e[0m"
    echo -e "\e[1;34m  uat     \e[0m- User Acceptance Testing environment"
    echo -e "\e[1;34m  sandbox \e[0m- Sandbox environment for testing and development"
    echo -e "\e[1;34m  prod    \e[0m- Production environment"
    echo
    echo -e "\e[1;31mError: Please provide a valid environment.\e[0m"
    exit 1
}

# Get the environment argument
ENV=$1

# Define allowed environment options
case "$ENV" in
    sandbox)
        ENV_EXAMPLE_FILE_OVERRIDE=".env.example"
        ;;
    uat)
        ENV_EXAMPLE_FILE_OVERRIDE=".env.example.uat"
        ;;
    prod)
        ENV_EXAMPLE_FILE_OVERRIDE=".env.example.prod"
        ;;
    *)
        echo -e "\e[1;31mError: Invalid environment '$ENV'.\e[0m"
        usage
        ;;
esac

# Source setup_env.sh to set environment variables and create .tfvars
source setup_env.sh "$ENV_EXAMPLE_FILE_OVERRIDE"

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

# Ensure terraform is initialized and upgraded
terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

# Apply the Terraform configuration
terraform apply -var-file=.tfvars
