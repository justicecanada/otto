#!/bin/bash

# Install Velero if it does not exist
if ! kubectl get namespace/velero &>/dev/null; then

    echo "Installing Velero..."

    # Install Velero CRDs
    velero install --crds-only

    # Apply the service account with the identity client ID applied
    export VELERO_IDENTITY_CLIENT_ID="$(az identity show -g $RESOURCE_GROUP_NAME -n $VELERO_IDENTITY_NAME --subscription $SUBSCRIPTION_ID --query clientId -otsv)"
    envsubst < velero.yaml | kubectl apply -f -

    # Install Velero with the Azure provider and wait for it to complete
    velero install \
        --provider azure \
        --service-account-name velero \
        --pod-labels azure.workload.identity/use=true \
        --plugins velero/velero-plugin-for-microsoft-azure:v1.10.0 \
        --no-secret \
        --bucket $BACKUP_CONTAINER_NAME \
        --backup-location-config useAAD="true",resourceGroup=$RESOURCE_GROUP_NAME,storageAccount=$STORAGE_NAME,subscriptionId=$SUBSCRIPTION_ID \
        --snapshot-location-config apiTimeout=30,resourceGroup=$RESOURCE_GROUP_NAME,subscriptionId=$SUBSCRIPTION_ID \
        --wait

fi
