
#!/bin/bash
set -e

EXPIRY_DATE=$(date -u -d "30 minutes" '+%Y-%m-%dT%H:%MZ')

# Generate SAS token
AZURE_SAS_TOKEN=$(az storage container generate-sas \
    --account-name $AZURE_STORAGE_ACCOUNT_NAME \
    --account-key $AZURE_STORAGE_ACCOUNT_KEY \
    --name $BACKUP_CONTAINER_NAME \
    --permissions acdlrw \
    --expiry $EXPIRY_DATE \
    --https-only \
    --output tsv)

# Set variables
SOURCE_PATH="/data/media/"
DESTINATION_URL="https://${AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${BACKUP_CONTAINER_NAME}/media/?${AZURE_SAS_TOKEN}"

# Sync media files to Azure Storage
echo "Syncing media files to Azure Storage"
azcopy sync "$SOURCE_PATH" "$DESTINATION_URL" --recursive
