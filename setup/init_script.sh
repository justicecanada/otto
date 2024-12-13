#!/bin/bash

# Get the credentials for the AKS cluster and exit if it fails
if ! az aks get-credentials --resource-group "$RESOURCE_GROUP_NAME" --name "$AKS_CLUSTER_NAME" --overwrite-existing; then
    echo "Failed to get AKS credentials. Exiting..."
    exit 0
fi

# Convert the kubeconfig to use the Azure CLI login mode, which utilizes the already logged-in context from Azure CLI to obtain the access token
kubelogin convert-kubeconfig -l azurecli

# Get the pod name of the Django app
export COORDINATOR_POD=$(kubectl get pods -n otto -l app=django-app -o jsonpath='{.items[0].metadata.name}')

# Run the initial setup script in the Django app pod
kubectl exec -it $COORDINATOR_POD -n otto -- env OTTO_ADMIN="${OTTO_ADMIN}" /django/initial_setup.sh
