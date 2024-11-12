#!/bin/bash

# Set timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Backup Django DB
DJANGO_BACKUP_FILE="/tmp/django_backup_$TIMESTAMP.sql"
pg_dump -h $DJANGODB_HOST -U $DJANGODB_USER -d $DJANGODB_NAME > $DJANGO_BACKUP_FILE

# Backup Vector DB
VECTOR_BACKUP_FILE="/tmp/vector_backup_$TIMESTAMP.sql"
pg_dump -h $VECTORDB_HOST -U $VECTORDB_USER -d $VECTORDB_NAME > $VECTOR_BACKUP_FILE

# Upload to Azure Storage
az storage blob upload --account-name $AZURE_STORAGE_ACCOUNT_NAME --container-name $BACKUP_CONTAINER_NAME --name $TIMESTAMP/django_backup.sql --file $DJANGO_BACKUP_FILE
az storage blob upload --account-name $AZURE_STORAGE_ACCOUNT_NAME --container-name $BACKUP_CONTAINER_NAME --name $TIMESTAMP/vector_backup.sql --file $VECTOR_BACKUP_FILE

# Clean up local files
rm $DJANGO_BACKUP_FILE $VECTOR_BACKUP_FILE
