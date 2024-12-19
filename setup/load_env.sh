#!/bin/bash

# Description:
# This script performs the following steps:
# 1. Parses command-line arguments for an optional .env file
# 2. Lists available .env files and prompts user to select one if not provided
# 3. Loads the selected .env file and generates dynamic environment variables
# 4. Checks Azure login status and logs in if necessary (with special handling for jumpbox)
# 5. Sets the correct Azure subscription and verifies Owner role permissions
# 6. Checks for existing management storage account across the subscription:
#    - If found, verifies it's in the correct resource group
#    - If not found, generates a unique name for a new storage account
# 7. Checks for existing jumpbox across the subscription:
#    - If found, verifies it's in the correct resource group
#    - If found and script is not running on jumpbox, exits with an error
# 8. Sets up Terraform environment directory

# This script ensures that only one management storage account and jumpbox
# exist per subscription and that they are in the correct resource group.
# It prepares the environment for subsequent Terraform operations.

# Function to select and load environment file
select_and_load_env() {
    local env_file="$1"
    
    if [ -z "$env_file" ]; then
        echo "Available .env files:"
        ls -1 .env*
        read -p "Specify the .env file to use: " env_file
    fi

    if [ -z "$env_file" ]; then
        echo "No .env file specified. Exiting..."
        return 1
    fi

    echo "Using $env_file as the .env file"

    # Load environment variables
    set -a
    source "$env_file"
    set +a

    # Resource Groups
    export MGMT_RESOURCE_GROUP_NAME="rg-ottomgmt-${ENV,,}"
    export APP_RESOURCE_GROUP_NAME="rg-ottoapp-${ENV,,}"

    # Compute Resources
    export JUMPBOX_NAME="vm-otto-${ENV,,}"
    export JUMPBOX_IDENTITY_NAME="id-otto-${ENV,,}"
    export AKS_CLUSTER_NAME="aks-otto-${ENV,,}"

    # Networking Resources
    export BASTION_NAME="bastion-otto-${ENV,,}"
    export FIREWALL_NAME="fw-otto-${ENV,,}"

    # Key Vault (regional scope)
    export KEYVAULT_NAME="kv-otto-${ENV,,}"

    # AI Services
    export COGNITIVE_SERVICES_NAME="ai-otto-${ENV,,}"
    export OPENAI_SERVICE_NAME="oai-otto-${ENV,,}"

    # Storage
    export DISK_NAME="disk-otto-${ENV,,}"

    # Managed Identities
    export VELERO_IDENTITY_NAME="id-ottobak-${ENV,,}"

    # Other variables that don't follow resource naming conventions
    export SITE_URL="https://${DOMAIN_NAME}"
    export TENANT_ID=$(az account show --query tenantId --output tsv)
    export ENTRA_AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"
    export TAGS="ApplicationName=${APP_NAME} Environment=${ENVIRONMENT} Location=${LOCATION} Classification=${CLASSIFICATION} CostCenter=\"${COST_CENTER}\" Criticality=${CRITICALITY} Owner=\"${OWNER}\""
}

# Function to check if we're on the jumpbox and login if necessary
check_and_login() {
    local is_jumpbox=$(hostname 2>/dev/null | grep -q "^$JUMPBOX_NAME$" && echo true || echo false)
    unset ARM_USE_MSI ARM_CLIENT_ID ARM_USE_CLI

    if ! az account show &>/dev/null; then
        if $is_jumpbox; then
            echo "Logging in with jumpbox identity..."
            az login --identity --username "$JUMPBOX_IDENTITY_NAME" --only-show-errors --output none
        else
            echo "Not logged in. Please run 'az login' to authenticate."
            return 1
        fi
    fi

    local current_user=$(az account show --query user.name -o tsv)
    if [[ "$current_user" == *"$JUMPBOX_IDENTITY_NAME"* ]] || $is_jumpbox; then
        echo "Using jumpbox identity."
        export ARM_USE_MSI=true
        export ARM_CLIENT_ID=$(az identity show --name $JUMPBOX_IDENTITY_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query clientId -o tsv)
    else
        echo "Logged in as $current_user"
        export ARM_USE_CLI=true
    fi
}

# Function to set the correct subscription
check_and_set_subscription() {
    local subscription_name="$1"
    local current_user_id=$(az ad signed-in-user show --query id -o tsv)
    
    # Get subscription ID
    local subscription_id=$(az account list --query "[?name=='$subscription_name'].id" -o tsv)
    if [ -z "$subscription_id" ]; then
        echo "Subscription '$subscription_name' not found. Exiting..."
        return 1
    fi

    # Set the subscription
    echo "Setting subscription to: $subscription_name (ID: $subscription_id)"
    if ! az account set --subscription "$subscription_id" --only-show-errors; then
        echo "Failed to set subscription. Exiting..."
        return 1
    fi

    # Check if the user has Owner role
    local role_assignment=$(az role assignment list --assignee "$current_user_id" --role "Owner" --scope "/subscriptions/$subscription_id" --query "[].roleDefinitionName" -o tsv)
    
    if [ "$role_assignment" != "Owner" ]; then
        echo "Error: You do not have the Owner role for subscription '$subscription_name'."
        return 1
    fi

    export ARM_SUBSCRIPTION_ID=$subscription_id
    export ARM_TENANT_ID=$(az account show --query tenantId -o tsv)

    echo "Successfully set subscription and verified Owner role."
    return 0
}

# Function to get unique mgmt storage account name
check_and_set_mgmt_storage_account_name() {
    local base_name="stottomgmt${ENV,,}"
    base_name=$(echo "$base_name" | tr '[:upper:]' '[:lower:]')
    
    local existing_account=$(az storage account list --query "[?starts_with(name, '$base_name')].{name:name, resourceGroup:resourceGroup}" -o tsv)
    
    if [ -n "$existing_account" ]; then
        local name=$(echo "$existing_account" | cut -f1)        
        export MGMT_STORAGE_NAME="$name"
    else
        local random_suffix=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)
        export MGMT_STORAGE_NAME="${base_name}${random_suffix}"
    fi
    
    echo "Storage account name: $MGMT_STORAGE_NAME"
    return 0
}

# Function to get unique app storage account name
check_and_set_app_storage_account_name() {
    local base_name="stottoapp${ENV,,}"
    base_name=$(echo "$base_name" | tr '[:upper:]' '[:lower:]')
    
    local existing_account=$(az storage account list --query "[?starts_with(name, '$base_name')].{name:name, resourceGroup:resourceGroup}" -o tsv)
    
    if [ -n "$existing_account" ]; then
        local name=$(echo "$existing_account" | cut -f1)        
        export APP_STORAGE_NAME="$name"
    else
        local random_suffix=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)
        export APP_STORAGE_NAME="${base_name}${random_suffix}"
    fi
    
    echo "Storage account name: $APP_STORAGE_NAME"
    return 0
}

# Function to get unique container registry name
check_and_set_acr_name() {
    local base_name="crotto${ENV,,}"
    base_name=$(echo "$base_name" | tr '[:upper:]' '[:lower:]')
    
    local existing_acr=$(az acr list --query "[?starts_with(name, '$base_name')].{name:name}" -o tsv)
    
    if [ -n "$existing_acr" ]; then
        local name="$existing_acr"
        local rg=$(az acr show --name "$name" --query resourceGroup -o tsv)

        if [ "$rg" != "$MGMT_RESOURCE_GROUP_NAME" ]; then
            echo "Error: Existing ACR '$name' found in resource group '$rg', which doesn't match MGMT_RESOURCE_GROUP_NAME '$MGMT_RESOURCE_GROUP_NAME'." >&2
            return 1
        fi

        export ACR_NAME="$name"
    else
        local random_suffix=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)
        export ACR_NAME="${base_name}${random_suffix}"
    fi
    
    echo "ACR name: $ACR_NAME"
    return 0
}

# Function to check if jumpbox exists and validate execution environment
check_jumpbox() {
    local jumpbox_name="$1"

    local existing_jumpbox=$(az vm list --query "[?name=='$jumpbox_name'].{name:name, resourceGroup:resourceGroup}" -o tsv)
    
    if [ -n "$existing_jumpbox" ]; then
        local rg=$(echo "$existing_jumpbox" | cut -f2)
        
        if [ "$rg" != "$MGMT_RESOURCE_GROUP_NAME" ]; then
            echo "Error: Existing jumpbox '$jumpbox_name' found in resource group '$rg', which doesn't match MGMT_RESOURCE_GROUP_NAME '$MGMT_RESOURCE_GROUP_NAME'." >&2
            return 1
        fi
        
        if [ "$(hostname)" != "$jumpbox_name" ]; then
            echo "Jumpbox already exists. Please run this script from the jumpbox." >&2
            return 1
        fi
    fi

    return 0
}

# Main execution
# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --env-file)
        env_file="$2"
        shift 2
        ;;
        *)
        shift
        ;;
    esac
done

select_and_load_env "$env_file"

if ! check_and_login; then
    echo "Script execution terminated due to login failure."
    return 1
fi

if ! check_and_set_subscription "$SUBSCRIPTION_NAME"; then
    echo "Script execution terminated due to subscription setting or permission failure."
    return 1
fi

if ! check_and_set_mgmt_storage_account_name; then
    echo "Script execution terminated due to storage account name error."
    return 1
fi

if ! check_and_set_app_storage_account_name; then
    echo "Script execution terminated due to storage account name error."
    return 1
fi

if ! check_jumpbox "$JUMPBOX_NAME"; then
    echo "Script execution terminated due to jumpbox validation failure."
    return 1
fi
