#!/bin/bash

# Description:
# This script performs the following steps:
# 1. Sets Terraform environment variables
# 2. Checks the current state of Terraform configuration
# 3. Handles different scenarios based on the state:
#    - Runs Terragrunt locally if state is local and not migratable
#    - Runs Terragrunt locally with state migration if state is local and migratable
#    - Prompts to make storage account private if state is remote and not private
#    - Runs Terragrunt with remote backend if state is remote
# 4. Initializes and applies Terragrunt configuration
# 5. Supports auto-approve functionality for non-interactive runs

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

check_current_tfstate() {
    local storage_account="$MGMT_STORAGE_NAME"
    local resource_group="$MGMT_RESOURCE_GROUP_NAME"
    local state_file_name="mgmt.tfstate"
    local timeout=10

    # Check if the storage account exists
    if ! az storage account show --name "$storage_account" --resource-group "$resource_group" &>/dev/null; then
        echo "Remote storage account doesn't exist yet. State file will be created locally. You can migrate it later."
        export TFSTATE_MIGRATABLE=false
        export TFSTATE_PRIVATE=false
        export TFSTATE_REMOTE=false
        export TFSTATE_BACKEND_ARGS=""
        return 0
    fi

    # Check if the storage account is private
    local is_public=$(timeout $timeout az storage account show --name "$storage_account" --resource-group "$resource_group" --query "allowBlobPublicAccess" --output tsv)
    if [ "$is_public" == "false" ]; then
        echo "Storage account is private. It cannot be made public."
        export TFSTATE_PRIVATE=true
    else
        echo "Storage account is not private. You can make it private later."
        export TFSTATE_PRIVATE=false
    fi

    # Try to list blobs in the container
    if ! state_file_path=$(timeout $timeout az storage blob list --account-name "$storage_account" --container-name "tfstate" --auth-mode login --query "[?ends_with(name, '/$state_file_name')].name" --output tsv 2>/dev/null); then
        echo "Unable to access storage account contents. Please check your permissions and make sure you are connecting from the jumpbox."
        return 1
    fi

    # Check if the state file exists
    if [ -n "$state_file_path" ]; then
        echo "State file exists in the remote location. Using remote state."
        export TFSTATE_REMOTE=true
        export TFSTATE_BACKEND_ARGS="-backend-config=resource_group_name=$MGMT_RESOURCE_GROUP_NAME \
            -backend-config=storage_account_name=$MGMT_STORAGE_NAME \
            -backend-config=container_name=tfstate \
            -backend-config=key=mgmt.tfstate"
    else
        echo "State file does not exist in the remote location yet. Using local state. You can migrate it later."
        export TFSTATE_REMOTE=false
        export TFSTATE_BACKEND_ARGS=""
    fi

    # Check if the state file is migratable
    if [ "$TFSTATE_PRIVATE" = false ] && [ "$TFSTATE_REMOTE" = true ]; then
        echo "State file is migratable."
        export TFSTATE_MIGRATABLE=true
    else
        echo "State file is not migratable."
        export TFSTATE_MIGRATABLE=false
    fi

    return 0
}

# Function to set Terraform environment variables
set_tfvars() {

    export TF_VAR_app_name="$APP_NAME"
    export TF_VAR_environment="$ENV"
    export TF_VAR_mgmt_resource_group_name="$MGMT_RESOURCE_GROUP_NAME"
    export TF_VAR_mgmt_storage_account_name="$MGMT_STORAGE_NAME"
    export TF_VAR_location="$LOCATION"
    export TF_VAR_keyvault_name="$KEYVAULT_NAME"
    export TF_VAR_vnet_name="$VNET_NAME"
    export TF_VAR_vnet_address_space="$VNET_IP_RANGE"
    export TF_VAR_mgmt_subnet_name="$MGMT_SUBNET_NAME"
    export TF_VAR_mgmt_subnet_prefix="$MGMT_SUBNET_IP_RANGE"
    export TF_VAR_app_subnet_name="$APP_SUBNET_NAME"
    export TF_VAR_app_subnet_prefix="$APP_SUBNET_IP_RANGE"
    export TF_VAR_web_subnet_name="$WEB_SUBNET_NAME"
    export TF_VAR_web_subnet_prefix="$WEB_SUBNET_IP_RANGE"
    export TF_VAR_jumpbox_name="$JUMPBOX_NAME"
    export TF_VAR_jumpbox_identity_name="$JUMPBOX_IDENTITY_NAME"
    export TF_VAR_otto_identity_name="$OTTO_IDENTITY_NAME"
    export TF_VAR_velero_identity_name="$VELERO_IDENTITY_NAME"
    export TF_VAR_bastion_name="$BASTION_NAME"
    export TF_VAR_bastion_vnet_name="$BASTION_VNET_NAME"
    export TF_VAR_bastion_vnet_address_space="$BASTION_VNET_IP_RANGE"
    export TF_VAR_bastion_subnet_name="$BASTION_SUBNET_NAME"
    export TF_VAR_bastion_subnet_prefix="$BASTION_SUBNET_IP_RANGE"
    export TF_VAR_firewall_name="$FIREWALL_NAME"
    export TF_VAR_firewall_vnet_name="$FIREWALL_VNET_NAME"
    export TF_VAR_firewall_vnet_address_space="$FIREWALL_VNET_IP_RANGE"
    export TF_VAR_firewall_subnet_name="$FIREWALL_SUBNET_NAME"
    export TF_VAR_firewall_subnet_prefix="$FIREWALL_SUBNET_IP_RANGE"
    export TF_VAR_acr_name="$ACR_NAME"
    export TF_VAR_aks_ingress_private_ip="$AKS_INGRESS_IP"
    export TF_VAR_tags="{\"Application\":\"$APP_NAME\",\"Environment\":\"$ENVIRONMENT\",\"Location\":\"$LOCATION\",\"Classification\":\"$CLASSIFICATION\",\"CostCenter\":\"$COST_CENTER\",\"Criticality\":\"$CRITICALITY\",\"Owner\":\"$OWNER\"}"
    
    echo "Environment variables for Terraform have been set."
    return 0
}

# Function to run Terragrunt locally without backend configuration or state migration
run_local_terragrunt() {
    echo "Initializing Terragrunt..."

    # We can't make the storage private yet because we need to migrate the TF state first
    export TF_VAR_mgmt_storage_make_private=false

    # Use the local Terragrunt configuration file
    export TERRAGRUNT_CONFIG=terragrunt-local.hcl

    if ! terragrunt init; then
        echo "Terragrunt initialization failed. Exiting..."
        return 1
    fi

    # If TF_CLI_ARGS_apply is not set, inform the user that the apply will require manual approval before executing the plan
    if [ -z "$TF_CLI_ARGS_apply" ]; then
        echo "Preparing Terragrunt plan. This will require manual approval before applying."
    else
        echo "Applying Terragrunt configuration."
    fi

    if ! terragrunt apply; then
        echo "Terragrunt apply failed. Exiting..."
        return 1
    fi
}

# Function to run Terragrunt with backend configuration and state migration
migrate_state() {
    echo "Migrating state to remote storage..."
    
    if ! terragrunt init $TFSTATE_BACKEND_ARGS -migrate-state; then
        echo "State migration failed. Exiting..."
        return 1
    fi

    echo "State migration to remote storage complete."    
}

# Function to run Terragrunt with backend configuration
run_remote_terragrunt() {

    # Use the default Terragrunt configuration file for remote backend
    unset TERRAGRUNT_CONFIG

    if [ "$TFSTATE_PRIVATE" = false ]; then
        read -p "The storage account is not currently private. Would you like to make it private? This change cannot be undone. (y/N)"
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "Making the storage account private..."
            export TF_VAR_mgmt_storage_make_private=true
        else
            echo "The storage account will not be made private."
            export TF_VAR_mgmt_storage_make_private=false
        fi
    fi

    echo "Initializing Terragrunt..."
    if ! terragrunt init $TFSTATE_BACKEND_ARGS; then
        echo "Terragrunt initialization failed. Exiting..."
        return 1
    fi

    if [ -z "$TF_CLI_ARGS_apply" ]; then
        echo "Preparing Terragrunt configuration. This will require manual approval."
    else
        echo "Applying Terragrunt configuration."
    fi

    if ! terragrunt apply $TFSTATE_BACKEND_ARGS; then
        echo "Terragrunt apply failed. Exiting..."
        return 1
    fi
}


# Main execution

# Default values
auto_approve=false

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --auto-approve)
        auto_approve="$2"
        shift 2
        ;;
        *)
        shift
        ;;
    esac
done

# Set TF_CLI_ARGS_apply based on auto_approve value
if [ "$auto_approve" = true ]; then
    export TF_CLI_ARGS_apply="-auto-approve"
else
    unset TF_CLI_ARGS_apply
fi

echo "Changing directory to terraform/mgmt..."
cd terraform/mgmt || return 1

if ! set_tfvars; then
    echo "Script execution terminated due to environment variable setting failure."
    cd ../../ && return 1
fi

if ! check_current_tfstate; then
    cd ../../ && return 1
fi

# If the state is local and not migratable, run the Terragrunt locally without backend configuration or state migration
if [ "$TFSTATE_REMOTE" = false ] && [ "$TFSTATE_MIGRATABLE" = false ]; then
    echo "Running Terragrunt without backend configuration or state migration..."
    run_local_terragrunt

    if ! check_current_tfstate; then
        cd ../../ && return 1
    fi
fi

# If the state is local and migratable, run the Terragrunt locally with backend configuration and state migration
if [ "$TFSTATE_REMOTE" = false ] && [ "$TFSTATE_MIGRATABLE" = true ]; then
    echo "Running Terragrunt with backend configuration and state migration..."
    run_local_terragrunt

    if ! check_current_tfstate; then
        cd ../../ && return 1
    fi
fi

# If the state is remote, run Terragrunt with backend configuration
if [ "$TFSTATE_REMOTE" = true ]; then
    echo "Running Terragrunt with backend configuration..."
    run_remote_terragrunt
    
    if ! check_current_tfstate; then
        cd ../../ && return 1
    fi
fi



# Return to the root directory when done
cd ../../