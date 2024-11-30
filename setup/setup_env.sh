#!/bin/bash

export ENV_FILE
export SUBSCRIPTION_ID
export ENTRA_CLIENT_ID
export DNS_SUBSCRIPTION_ID
export DNS_RESOURCE_GROUP
export MGMT_RESOURCE_GROUP_NAME
export MGMT_STORAGE_NAME
export ACR_NAME
export JUMPBOX_NAME
export ADMIN_GROUP_ID
export LOG_ANALYTICS_READERS_GROUP_ID
export VNET_ID
export WEB_SUBNET_ID
export APP_SUBNET_ID

# Check and install Azure CLI if not present
if ! command -v az &> /dev/null; then
    echo "Installing Azure CLI"
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
fi

# Check and install docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker"
    sudo apt update
    sudo apt install -y apt-transport-https ca-certificates curl software-properties-common
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt update
    sudo apt install -y docker-ce docker-ce-cli containerd.io
    sudo usermod -aG docker azureuser
    newgrp docker
fi

# Check and install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo "Installing Certbot"
    sudo apt update
    sudo apt install -y certbot
fi

# Login to Azure if not already logged in
az account show &> /dev/null || az login --identity --only-show-errors --output none

# Set the subscription
[ -n "$SUBSCRIPTION_ID" ] && az account set --subscription "$SUBSCRIPTION_ID" --only-show-errors --output none

export SITE_URL="${DNS_LABEL}.${DNS_ZONE}"
export HOST_NAME=${SITE_URL#https://}

# Set the environment variables
export TENANT_ID=$(az account show --query tenantId --output tsv)
export ENTRA_AUTHORITY="https://login.microsoftonline.com/${TENANT_ID}"

# Set the dynamically generated variables
export RESOURCE_GROUP_NAME="${APP_NAME}${INTENDED_USE^^}Rg"
export KEYVAULT_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-kv"
export COGNITIVE_SERVICES_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-cs"
export OPENAI_SERVICE_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-openai"
export AKS_CLUSTER_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-aks"
export DISK_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-disk"
export ACR_NAME="${ORGANIZATION,,}${INTENDED_USE,,}${APP_NAME,,}acr"
export DJANGODB_RESOURCE_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-db"
#export VELERO_IDENTITY_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-velero"
export TAGS="ApplicationName=${APP_NAME} Environment=${ENVIRONMENT} Location=${LOCATION} Classification=${CLASSIFICATION} CostCenter=\"${COST_CENTER}\" Criticality=${CRITICALITY} Owner=\"${OWNER}\""



# Set a globally unique storage account name
storage_name="${ORGANIZATION,,}${INTENDED_USE,,}${APP_NAME,,}store" # Base name for the storage account
existing_storage=$(az storage account list --resource-group "$RESOURCE_GROUP_NAME" --query "[?starts_with(name, '${storage_name}')].name" -o tsv)
if [ -z "$existing_storage" ]; then
    # Random 5-digit alphanumeric suffix
    random_suffix=$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 5 | head -n 1)
    export STORAGE_NAME="${storage_name}${random_suffix}"
else
    export STORAGE_NAME="$existing_storage"
fi


# If the backups container doesn't exist, create it
export BACKUP_CONTAINER_NAME="backups"
if ! az storage container show \
        --name "$BACKUP_CONTAINER_NAME" \
        --account-name "$MGMT_STORAGE_NAME" \
        --auth-mode login \
        --only-show-errors &>/dev/null; then
    echo "Creating blob container: $BACKUP_CONTAINER_NAME"
    az storage container create \
        --name "$BACKUP_CONTAINER_NAME" \
        --account-name "$MGMT_STORAGE_NAME" \
        --auth-mode login \
        --only-show-errors &>/dev/null
else
    echo "Blob container $BACKUP_CONTAINER_NAME already exists."
fi



# Set the Terraform state variables
export TF_STATE_CONTAINER="tfstate"
export TF_STATE_KEY="${RESOURCE_GROUP_NAME}.tfstate"

# If the Terraform state container doesn't exist, create it
if ! az storage container show \
        --name "$TF_STATE_CONTAINER" \
        --account-name "$STORAGE_NAME" \
        --auth-mode login \
        --only-show-errors &>/dev/null; then
    echo "Creating blob container: $TF_STATE_CONTAINER"
    az storage container create \
        --name "$TF_STATE_CONTAINER" \
        --account-name "$STORAGE_NAME" \
        --auth-mode login \
        --only-show-errors &>/dev/null
else
    echo "Blob container $TF_STATE_CONTAINER already exists."
fi


# Create terraform/.tfvars file
cat > terraform/.tfvars <<EOF
app_name = "${APP_NAME}"
environment = "${ENVIRONMENT}"
location = "${LOCATION}"
classification = "${CLASSIFICATION}"
cost_center = "${COST_CENTER}"
criticality = "${CRITICALITY}"
owner = "${OWNER}"
admin_group_id = "${ADMIN_GROUP_ID"
log_analytics_readers_group_id = "${LOG_ANALYTICS_READERS_GROUP_ID}"
acr_id = "${ACR_ID}"
resource_group_name = "${RESOURCE_GROUP_NAME}"
keyvault_name = "${KEYVAULT_NAME}"
cognitive_services_name = "${COGNITIVE_SERVICES_NAME}"
openai_service_name = "${OPENAI_SERVICE_NAME}"
aks_cluster_name = "${AKS_CLUSTER_NAME}"
disk_name = "${DISK_NAME}"
storage_name = "${STORAGE_NAME}"
djangodb_resource_name = "${DJANGODB_RESOURCE_NAME}"
approved_cpu_quota = "${APPROVED_CPU_QUOTA}"
vnet_id = "${VNET_ID}"
web_subnet_id = "${WEB_SUBNET_ID}"
app_subnet_id = "${APP_SUBNET_ID}"
gpt_35_turbo_capacity = ${GPT_35_TURBO_CAPACITY}
gpt_4_turbo_capacity = ${GPT_4_TURBO_CAPACITY}
gpt_4o_capacity = ${GPT_4o_CAPACITY}
gpt_4o_mini_capacity = ${GPT_4o_MINI_CAPACITY}
text_embedding_3_large_capacity = ${TEXT_EMBEDDING_3_LARGE_CAPACITY}
admin_email = "${ADMIN_EMAIL}"
use_private_network = "${USE_PRIVATE_NETWORK}"
backup_container_name = "${BACKUP_CONTAINER_NAME}"
EOF
