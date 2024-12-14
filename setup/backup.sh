#!/bin/bash
set -e

# Set timestamp and extract date components
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
YEAR=$(date +%Y)
MONTH=$(date +%m)
DAY=$(date +%d)

# Function to perform backup
perform_backup() {
    local DB_TYPE=$1
    local DB_HOST=$2
    local DB_USER=$3
    local DB_NAME=$4
    local BACKUP_FILE="/tmp/${DB_TYPE}_backup_${TIMESTAMP}.sql.gz"

    echo "Backing up $DB_TYPE database to $BACKUP_FILE"
    pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"

    echo "Uploading $DB_TYPE backup to Azure Storage"
    az storage blob upload --account-name "$AZURE_STORAGE_ACCOUNT_NAME" --account-key "$AZURE_STORAGE_ACCOUNT_KEY" --container-name "$BACKUP_CONTAINER_NAME" --name "db/$YEAR/$MONTH/$DAY/${DB_TYPE}_backup_${TIMESTAMP}.sql.gz" --file "$BACKUP_FILE" --overwrite > /dev/null
    
    rm "$BACKUP_FILE"
}

# Perform backups
export PGPASSWORD=$DJANGODB_PASSWORD
perform_backup "django" "$DJANGODB_HOST" "$DJANGODB_USER" "$DJANGODB_NAME"

export PGPASSWORD=$VECTORDB_PASSWORD
perform_backup "vector" "$VECTORDB_HOST" "$VECTORDB_USER" "$VECTORDB_NAME"

echo "Backup completed successfully
