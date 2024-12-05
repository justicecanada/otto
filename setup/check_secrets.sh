#!/bin/bash

# Fetch the expiry date of the Entra client secret from the environment and convert it to a timestamp
ENTRA_CLIENT_SECRET_EXPIRY=${ENTRA_CLIENT_SECRET_EXPIRY:-"1900-01-01"}
expiry_timestamp=$(date -d "$ENTRA_CLIENT_SECRET_EXPIRY" +%s)
current_timestamp=$(date +%s)
thirty_days_later=$(date -d '+30 days' +%s)

# Function to check if a secret exists in Key Vault
check_secret() {
    if az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$1" &>/dev/null; then
        return 0  # Secret exists
    else
        return 1  # Secret doesn't exist
    fi
}

# Function to get the current value of a secret from Key Vault
get_secret_value() {
    az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$1" --query "value" -o tsv
}

# Function to set or update a secret in Key Vault
set_secret() {
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "$1" --value "$2" > /dev/null
    echo "$1 has been set or updated."
}

update_secret() {
    # TODO: Grant the jumpbox-identity 'Application Administrator' for the Entra app and let it handle the secret rotation automatically

    if [ "$expiry_timestamp" -ge "$thirty_days_later" ]; then
        echo "ENTRA_CLIENT_SECRET expiry date is valid and set to: $ENTRA_CLIENT_SECRET_EXPIRY."
        return 0
    fi

    status=$([ "$expiry_timestamp" -lt "$current_timestamp" ] && echo "expired" || echo "will expire soon")
    echo "Warning: The ENTRA_CLIENT_SECRET $status (on $ENTRA_CLIENT_SECRET_EXPIRY)."
    
    read -p "Do you want to update the secret now? (y/N): " -n 1 -r update_choice
    echo
    [[ ! $update_choice =~ ^[Yy]$ ]] && echo "Continuing with the existing secret." && return 1

    read -p "Enter new expiry date (YYYY-MM-DD): " new_expiry
    export ENTRA_CLIENT_SECRET_EXPIRY="$new_expiry"
    
    echo "Instructions for updating Key Vault:"
    echo "1. Generate a new client secret in Azure Portal for the app registration: $APP_NAME"
    echo "2. Run: az keyvault secret set --vault-name $KEYVAULT_NAME --name 'ENTRA-CLIENT-SECRET' --value '<new_secret_value>' --expires '$new_expiry'"
    
    sed -i "s/ENTRA_CLIENT_SECRET_EXPIRY=.*/ENTRA_CLIENT_SECRET_EXPIRY=$new_expiry/" "$ENV_FILE"
    echo "New expiry date set to: $new_expiry"
    
    read -p "Have you updated the secret in Key Vault? (y/N): " -n 1 -r confirm
    echo
    [[ ! $confirm =~ ^[Yy]$ ]] && echo "Please update the secret in Key Vault before continuing." && exit 1
}

update_secret

# Check and set ENTRA-CLIENT-SECRET
check_entra_client_secret() {
    if ! check_secret "ENTRA-CLIENT-SECRET"; then
        echo "ENTRA-CLIENT-SECRET not found in Key Vault."
        update_secret
    else
        update_secret
    fi

    # If update_secret didn't set a new secret, prompt for one
    if ! check_secret "ENTRA-CLIENT-SECRET"; then
        read -s -p "Enter the Entra client secret: " entra_client_secret
        echo
        set_secret "ENTRA-CLIENT-SECRET" "$entra_client_secret"
        unset entra_client_secret
    fi
}
check_entra_client_secret


# Check and set VECTORDB-PASSWORD
if ! check_secret "VECTORDB-PASSWORD"; then
    password=$(openssl rand -base64 16 | tr -d /=+ | cut -c -16)
    set_secret "VECTORDB-PASSWORD" "$password"
    unset password
fi

# Check and set DJANGODB-PASSWORD
if ! check_secret "DJANGODB-PASSWORD"; then
    password=$(openssl rand -base64 16 | tr -d /=+ | cut -c -16)
    set_secret "DJANGODB-PASSWORD" "$password"
    unset password
fi

# Check and set DJANGO-SECRET-KEY
if ! check_secret "DJANGO-SECRET-KEY"; then
    secret_key=$(openssl rand -base64 50 | tr -d /=+ | cut -c -50)
    set_secret "DJANGO-SECRET-KEY" "$secret_key"
    unset secret_key
fi


# Check and set STORAGE-KEY
if ! az storage account show --name "$STORAGE_NAME" --resource-group "$RESOURCE_GROUP_NAME" &>/dev/null; then
    echo "Storage account $STORAGE_NAME does not exist."
else
    storage_key=$(az storage account keys list --account-name "$STORAGE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query '[0].value' -o tsv)
    if ! check_secret "STORAGE-KEY"; then
        set_secret "STORAGE-KEY" "$storage_key"
    else
        current_storage_key=$(get_secret_value "STORAGE-KEY")
        if [ "$storage_key" != "$current_storage_key" ]; then
            set_secret "STORAGE-KEY" "$storage_key"
        fi
        unset current_storage_key
    fi
    unset storage_key
fi

# Check and set OPENAI-SERVICE-KEY
if ! az cognitiveservices account show --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME" &>/dev/null; then
    echo "OpenAI service $OPENAI_SERVICE_NAME does not exist."
else
    openai_key=$(az cognitiveservices account keys list --name "$OPENAI_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query 'key1' -o tsv)
    if ! check_secret "OPENAI-SERVICE-KEY"; then
        set_secret "OPENAI-SERVICE-KEY" "$openai_key"
    else
        current_openai_key=$(get_secret_value "OPENAI-SERVICE-KEY")
        if [ "$openai_key" != "$current_openai_key" ]; then
            set_secret "OPENAI-SERVICE-KEY" "$openai_key"
        fi
        unset current_openai_key
    fi
    unset openai_key
fi

# Check and set COGNITIVE-SERVICE-KEY
if ! az cognitiveservices account show --name "$COGNITIVE_SERVICES_NAME" --resource-group "$RESOURCE_GROUP_NAME" &>/dev/null; then
    echo "Cognitive service $COGNITIVE_SERVICES_NAME does not exist."
else
    cognitive_key=$(az cognitiveservices account keys list --name "$COGNITIVE_SERVICES_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query 'key1' -o tsv)
    if ! check_secret "COGNITIVE-SERVICE-KEY"; then
        set_secret "COGNITIVE-SERVICE-KEY" "$cognitive_key"
    else
        current_cognitive_key=$(get_secret_value "COGNITIVE-SERVICE-KEY")
        if [ "$cognitive_key" != "$current_cognitive_key" ]; then
            set_secret "COGNITIVE-SERVICE-KEY" "$cognitive_key"
        fi
        unset current_cognitive_key
    fi
    unset cognitive_key
fi
