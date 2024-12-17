#!/bin/bash

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

# Check and set ENTRA-CLIENT-SECRET
if ! check_secret "ENTRA-CLIENT-SECRET"; then
    read -s -p "Enter the Entra client secret: " entra_client_secret
    set_secret "ENTRA-CLIENT-SECRET" "$entra_client_secret"
    unset entra_client_secret
fi

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