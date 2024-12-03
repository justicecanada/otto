#!/bin/bash

# Default values
AUTO_APPROVE=""
ENABLE_DEBUG=""
ENV_FILE=""
SUBSCRIPTION=""
SKIP_CONFIRM=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --auto-approve)
        AUTO_APPROVE="$2"
        shift 2
        ;;
        --enable-debug)
        ENABLE_DEBUG="$2"
        shift 2
        ;;
        --env-file)
        ENV_FILE="$2"
        shift 2
        ;;
        --subscription)
        SUBSCRIPTION="$2"
        shift 2
        ;;
        --skip-confirm)
        SKIP_CONFIRM="$2"
        shift 2
        ;;
        *)
        # Unknown option
        echo "Unknown option: $1"
        exit 1
        ;;
    esac
done


# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Function to clean up temporary files
cleanup() {
    rm -f backend_config.hcl
    unset TF_VAR_entra_client_secret
}
trap cleanup EXIT

# Source setup_env.sh to set environment variables and create .tfvars
source setup_env.sh --env-file "$ENV_FILE" --subscription "$SUBSCRIPTION" --skip-confirm "$SKIP_CONFIRM"

# Ask if the user wants to auto-approve the Terraform plan
unset TF_CLI_ARGS_apply
if [[ -z "$AUTO_APPROVE" ]]; then
    read -p "Auto-approve the Terraform plan? (y/N): " AUTO_APPROVE
fi

if [[ $AUTO_APPROVE =~ ^[Yy]$ ]]; then
    # Set the auto-approve flag
    export TF_CLI_ARGS_apply="-auto-approve"
fi

# Function to ensure Terraform state storage exists
ensure_tf_state_storage() {
    echo "Ensuring Terraform state storage exists..."

    # Create or update resource group with tags
    if ! az group show \
            --name "$TF_STATE_RESOURCE_GROUP" \
            --only-show-errors &>/dev/null; then
        echo "Creating resource group: $TF_STATE_RESOURCE_GROUP"
        az group create \
            --name "$TF_STATE_RESOURCE_GROUP" \
            --location "$LOCATION" \
            --tags $TAGS \
            --only-show-errors &>/dev/null
    else
        echo "Resource group $TF_STATE_RESOURCE_GROUP already exists."
    fi

    # Create storage account if it doesn't exist
    if ! az storage account show \
            --name "$TF_STATE_STORAGE_ACCOUNT" \
            --resource-group "$TF_STATE_RESOURCE_GROUP" \
            --only-show-errors &>/dev/null; then
        echo "Creating storage account: $TF_STATE_STORAGE_ACCOUNT"
        az storage account create \
            --name "$TF_STATE_STORAGE_ACCOUNT" \
            --resource-group "$TF_STATE_RESOURCE_GROUP" \
            --location "$LOCATION" \
            --sku Standard_LRS \
            --kind StorageV2 \
            --encryption-services blob \
            --min-tls-version TLS1_2 \
            --allow-blob-public-access false \
            --tags $TAGS \
            --only-show-errors &>/dev/null
    else
        echo "Storage account $TF_STATE_STORAGE_ACCOUNT already exists."
    fi

    # Create blob container if it doesn't exist
    if ! az storage container show \
            --name "$TF_STATE_CONTAINER" \
            --account-name "$TF_STATE_STORAGE_ACCOUNT" \
            --auth-mode login \
            --only-show-errors &>/dev/null; then
        echo "Creating blob container: $TF_STATE_CONTAINER"
        az storage container create \
            --name "$TF_STATE_CONTAINER" \
            --account-name "$TF_STATE_STORAGE_ACCOUNT" \
            --auth-mode login \
            --only-show-errors &>/dev/null
    else
        echo "Blob container $TF_STATE_CONTAINER already exists."
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


# If ENABLE_DEBUG is blank, prompt the user if they want to enable debugging mode.
unset TF_LOG
if [[ -z "$ENABLE_DEBUG" ]]; then
    read -p "Enable Terraform debugging mode? (y/N): " ENABLE_DEBUG
fi

# Verify $CORPORATE_PUBLIC_IP is not empty
if [ -z "$CORPORATE_PUBLIC_IP" ]; then
    echo "CORPORATE_PUBLIC_IP is empty. Please set the environment variable."
    exit 1
fi

if [[ $ENABLE_DEBUG =~ ^[Yy]$ ]]; then

    # Set the Terraform log level to debug
    export TF_LOG=DEBUG

    # Set the timestamp for debugging logs
    export TIMESTAMP=$(date +%Y%m%d%H%M%S)

    # Make sure the debug directory exists
    mkdir -p .terraform/debug

    # Ensure terraform is initialized and upgraded
    terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure > ".terraform/debug/$TIMESTAMP-init.txt" 2>&1

    # Apply the Terraform configuration
    terraform apply -var-file=.tfvars > ".terraform/debug/$TIMESTAMP-apply.txt" 2>&1

else

    # Ensure terraform is initialized and upgraded
    terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

    # Apply the Terraform configuration
    terraform apply -var-file=.tfvars

fi

# Make sure the secrets are set
source ../check_secrets.sh

