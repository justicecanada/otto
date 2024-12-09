#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Ensure we're in the correct directory
cd /home/azureuser/otto/setup/terraform

# Clean up temporary files when the script exits
trap 'rm -f backend_config.hcl' EXIT

# Set flags for Terraform
export TF_CLI_ARGS_apply="-auto-approve"
export ARM_USE_MSI=true
export ARM_CLIENT_ID=$(az identity show --name $JUMPBOX_IDENTITY_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query clientId -o tsv)
export ARM_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
export ARM_TENANT_ID=$TENANT_ID


# Create a temporary backend configuration file
cat > backend_config.hcl << EOF
resource_group_name  = "$MGMT_RESOURCE_GROUP_NAME"
storage_account_name = "$MGMT_STORAGE_NAME"
container_name       = "$TF_STATE_CONTAINER"
key                  = "$TF_STATE_KEY"
EOF


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

cd /home/azureuser/otto/setup
