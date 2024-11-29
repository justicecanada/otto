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
