#!/bin/bash

# Function to check if a secret exists in Key Vault
check_secret() {
    az keyvault secret show --vault-name "$KEYVAULT_NAME" --name "$1" &>/dev/null
}

# Check and set ENTRA-CLIENT-SECRET
if ! check_secret "ENTRA-CLIENT-SECRET"; then
    read -s -p "Enter the Entra client secret: " entra_client_secret
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "ENTRA-CLIENT-SECRET" --value "$entra_client_secret" > /dev/null
    echo "ENTRA-CLIENT-SECRET has been set."
fi

# Check and set VECTORDB-PASSWORD
if ! check_secret "VECTORDB-PASSWORD"; then
    password=$(openssl rand -base64 16 | tr -d /=+ | cut -c -16)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "VECTORDB-PASSWORD" --value "$password" > /dev/null
    echo "VECTORDB-PASSWORD has been set."
fi

# Check and set DJANGODB-PASSWORD
if ! check_secret "DJANGODB-PASSWORD"; then
    password=$(openssl rand -base64 16 | tr -d /=+ | cut -c -16)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "DJANGODB-PASSWORD" --value "$password" > /dev/null
    echo "DJANGODB-PASSWORD has been set."
fi

# Check and set DJANGO-SECRET-KEY
if ! check_secret "DJANGO-SECRET-KEY"; then
    secret_key=$(openssl rand -base64 50 | tr -d /=+ | cut -c -50)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "DJANGO-SECRET-KEY" --value "$secret_key" > /dev/null
    echo "DJANGO-SECRET-KEY has been set."
fi

# Check and set STORAGE-KEY
if ! check_secret "STORAGE-KEY"; then
    storage_key=$(az storage account keys list --account-name "$STORAGE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query '[0].value' -o tsv)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "STORAGE-KEY" --value "$storage_key" > /dev/null
    echo "STORAGE-KEY has been set."
fi

# Check and set OPENAI-SERVICE-KEY
if ! check_secret "OPENAI-SERVICE-KEY"; then
    # SC-13: Secure storage of OpenAI key in Key Vault
    openai_key=$(az cognitiveservices account keys list --name "$OPENAI_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query 'key1' -o tsv)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "OPENAI-SERVICE-KEY" --value "$openai_key" > /dev/null
    echo "OPENAI-SERVICE-KEY has been set."
fi

# Check and set COGNITIVE-SERVICE-KEY
if ! check_secret "COGNITIVE-SERVICE-KEY"; then
    # SC-13: Secure storage of Cognitive Services key in Key Vault
    cognitive_key=$(az cognitiveservices account keys list --name "$COGNITIVE_SERVICES_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query 'key1' -o tsv)
    az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "COGNITIVE-SERVICE-KEY" --value "$cognitive_key" > /dev/null
    echo "COGNITIVE-SERVICE-KEY has been set."
fi
