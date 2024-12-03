#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Store the current directory
CURRENT_DIR=$(pwd)

# Function to clean up temporary files and return to the original directory
cleanup() {
    rm -f backend_config.hcl
    cd "$CURRENT_DIR"
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

# Function to run a command and handle errors
run_command() {
    "$@" || { echo "Error occurred while executing: $*"; exit 1; }
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

        # Convert comma-separated ADMIN_GROUP_ID to array
        IFS=',' read -ra ADMIN_GROUP_IDS <<< "$ADMIN_GROUP_ID"

        # Get current assignments with principal IDs in TSV format
        current_assignments=$(az role assignment list \
            --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.KeyVault/vaults/$KEYVAULT_NAME" \
            --role "Key Vault Administrator" \
            --query "[].{id:id,principalId:principalId}" -o tsv)

        # Process each line of the output
        while IFS=$'\t' read -r assignment_id principal_id; do
            if [[ -n $assignment_id && -n $principal_id ]]; then
                # Check if principal ID is in our admin group list
                for admin_id in "${ADMIN_GROUP_IDS[@]}"; do
                    if [[ "$principal_id" == "$admin_id" ]]; then
                        echo "Removing role assignment $assignment_id as it will be managed by Terraform..."
                        az role assignment delete --ids "$assignment_id"
                        break
                    fi
                done
            fi
        done <<< "$current_assignments"

        # Get the managed identity's object ID
        managed_identity_id=$(az identity show --name $JUMPBOX_IDENTITY_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query principalId -o tsv)

        # Check if role assignment already exists
        if ! az role assignment list \
            --assignee-object-id "$managed_identity_id" \
            --role "Key Vault Administrator" \
            --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.KeyVault/vaults/$KEYVAULT_NAME" \
            --query "[].id" -o tsv &>/dev/null; then
            
            echo "Assigning Key Vault Administrator role to managed identity..."
            az role assignment create \
                --role "Key Vault Administrator" \
                --assignee-object-id "$managed_identity_id" \
                --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.KeyVault/vaults/$KEYVAULT_NAME"

            # Inform the user of a propagation delay requirement and ask for confirmation before continuing
            echo "Role assignment created. Please wait for the permissions to propagate before continuing. Press any key to continue..."
            read -n 1 -s
        fi

        # If there is a customer-managed key in the keyvault, delete it so that Terraform can manage it
        if az keyvault key list --vault-name "$KEYVAULT_NAME" --query "[?managed==null].kid" -o tsv &>/dev/null; then
            echo "Key Vault contains customer-managed keys, deleting..."
            az keyvault key list --vault-name "$KEYVAULT_NAME" --query "[?managed==null].kid" -o tsv | xargs -I {} az keyvault key delete --vault-name "$KEYVAULT_NAME" --name "$(basename {})"
        fi

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

        # Delete it using the Azure CLI and purge it to release the quotas
        echo "OpenAI service exists but not in Terraform state, deleting..."
        az cognitiveservices account delete --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME"
        
        # Prompt the user to use the Portal to purge the service manually and wait for confirmation
        echo "OpenAI service deleted. Please go to the Azure Portal and purge the service to release the quotas. Press any key to continue..."
        read -n 1 -s
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
    run_command terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure > ".terraform/debug/$TIMESTAMP-init.txt" 2>&1

    # Apply the Terraform configuration
    run_command terraform apply -var-file=.tfvars > ".terraform/debug/$TIMESTAMP-apply.txt" 2>&1

else

    # Ensure terraform is initialized and upgraded
    run_command terraform init -backend-config=backend_config.hcl -backend-config="access_key=$TFSTATE_ACCESS_KEY" -upgrade -reconfigure

    # Apply the Terraform configuration
    run_command terraform apply -var-file=.tfvars

fi
