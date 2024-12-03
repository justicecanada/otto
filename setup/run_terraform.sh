#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Store the current directory
CURRENT_DIR=$(pwd)

# Function to clean up temporary files and return to the original directory
cleanup() {
    rm -f backend_config.hcl
    cd $CURRENT_DIR
}
trap cleanup EXIT

# Set the auto-approve flag
export TF_CLI_ARGS_apply="-auto-approve"

# Change to the Terraform directory
cd terraform

# Create a temporary backend configuration file
cat > backend_config.hcl << EOF
resource_group_name  = "$MGMT_RESOURCE_GROUP_NAME"
storage_account_name = "$MGMT_STORAGE_NAME"
container_name       = "$TF_STATE_CONTAINER"
key                  = "$TF_STATE_KEY"
use_msi              = true
subscription_id      = "$SUBSCRIPTION_ID"
EOF

# Function to check if resource is already in Terraform state
check_terraform_state() {
    local resource_address=$1
    terraform state list | grep -q "^${resource_address}$"
    return $?
}

# Import the resource group if it exists and is not in the Terraform state
if az group show --name "$RESOURCE_GROUP_NAME" --query id -o tsv &>/dev/null; then
    if ! check_terraform_state "module.resource_group.azurerm_resource_group.rg"; then
        echo "Resource group exists but not in Terraform state, importing..."
        terraform import -var-file=.tfvars "module.resource_group.azurerm_resource_group.rg" \
            "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME"
    fi
fi

# Import the key vault if it exists and is not in the Terraform state
if az keyvault show --name "$KEYVAULT_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query id -o tsv &>/dev/null; then
    if ! check_terraform_state "module.keyvault.azurerm_key_vault.kv"; then
        echo "Key Vault exists but not in Terraform state, importing..."
        terraform import -var-file=.tfvars "module.keyvault.azurerm_key_vault.kv" \
            "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.KeyVault/vaults/$KEYVAULT_NAME"
    fi
fi

# If the OpenAI service exists and is not in the Terraform state, delete it and let Terraform recreate it
if az cognitiveservices account show --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query id -o tsv &>/dev/null; then
    if ! check_terraform_state "module.openai.azurerm_cognitive_account.openai"; then
        # Because the OpenAI service is not supported by the Azure CLI, we cannot import it; we must delete it and let Terraform recreate it
        read -p "OpenAI service exists but cannot be imported into Terraform state. Do you want to delete it and let Terraform recreate it? (y/n) " -n 1 -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Exiting..."
            exit 1
        fi
        echo "OpenAI service exists but not in Terraform state, deleting..."
        
        # Delete it using the Azure CLI and purge it to release the quotas
        az cognitiveservices account delete --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME"
        az cognitiveservices account purge --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --location "$LOCATION"
    fi
fi

# Import the Cognitive Services if it exists and is not in the Terraform state
if az cognitiveservices account show --name "$COGNITIVE_SERVICES_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query id -o tsv &>/dev/null; then
    if ! check_terraform_state "module.cognitive_services.azurerm_cognitive_account.cognitive_services"; then
        echo "Cognitive Services exists but not in Terraform state, importing..."
        terraform import -var-file=.tfvars "module.cognitive_services.azurerm_cognitive_account.cognitive_services" \
            "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.CognitiveServices/accounts/$COGNITIVE_SERVICES_NAME"
    fi
fi


# If ENABLE_DEBUG is true, set the Terraform log level to debug
unset TF_LOG
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
