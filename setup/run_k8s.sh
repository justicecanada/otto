#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Ensure we're in the correct directory
cd /home/azureuser/otto/setup/k8s

# Get the credentials for the AKS cluster and exit if it fails
if ! az aks get-credentials --resource-group "$RESOURCE_GROUP_NAME" --name "$AKS_CLUSTER_NAME" --overwrite-existing; then
    echo "Failed to get AKS credentials. Exiting..."
    exit 0
fi

# Convert the kubeconfig to use the Azure CLI login mode, which utilizes the already logged-in context from Azure CLI to obtain the access token
kubelogin convert-kubeconfig -l azurecli

export OTTO_IDENTITY_ID=$(az identity show \
  --name otto-identity \
  --resource-group $RESOURCE_GROUP_NAME \
  --query clientId \
  --output tsv)
  

# Apply the NGINX Ingress Controller
# kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.2/deploy/static/provider/cloud/deploy.yaml
# kubectl patch configmap ingress-nginx-controller -n ingress-nginx --type=merge -p '{"data":{"use-gzip":"true", "gzip-min-length":"1024"}}'
envsubst < ingress-nginx-controller.yaml | kubectl apply -f -

# Wait for the NGINX Ingress Controller to be ready
echo "Waiting for NGINX Ingress Controller to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/ingress-nginx-controller -n ingress-nginx
      
# Apply the Cert-Manager CRDs and Cert-Manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.crds.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.yaml

# Wait for cert-manager webhook to be ready
echo "Waiting for cert-manager webhook to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager

# Create the ClusterIssuer for Let's Encrypt
envsubst < letsencrypt-cluster-issuer.yaml | kubectl apply -f -

# Apply the namespace for Otto
kubectl apply -f namespace.yaml


# Apply the Kubernetes resources related to Otto, substituting environment variables where required

envsubst < configmap.yaml | kubectl apply -f -
envsubst < nginx-errors.yaml | kubectl apply -f -

# Apply the Ingress resource, substituting environment variables where required
if [[ "$USE_PRIVATE_NETWORK" == "true" ]]; then
    envsubst < ingress-private.yaml | kubectl apply -f -
else
    envsubst < ingress-public.yaml | kubectl apply -f -
fi

envsubst < secrets.yaml | kubectl apply -f -
envsubst < storageclass.yaml | kubectl apply -f -
envsubst < vectordb.yaml | kubectl apply -f -
envsubst < djangodb.yaml | kubectl apply -f -
envsubst < django.yaml | kubectl apply -f -
envsubst < redis.yaml | kubectl apply -f -
envsubst < celery.yaml | kubectl apply -f -

# Apply the Backup and Media Sync jobs
envsubst < db-backups.yaml | kubectl apply -f -
envsubst < media-sync.yaml | kubectl apply -f -

# Function to check if all deployments (except those containing "celery") are ready
check_deployments_ready() {
    local deployments=$(kubectl get deployments -n otto -o name | grep -v "deployment.apps/.*celery.*")
    for deployment in $deployments; do
        local ready=$(kubectl get $deployment -n otto -o jsonpath='{.status.readyReplicas}')
        local desired=$(kubectl get $deployment -n otto -o jsonpath='{.spec.replicas}')
        if [[ "$ready" != "$desired" ]]; then
            return 1
        fi
    done
    return 0
}

check_statefulsets_ready() {
    local statefulsets=$(kubectl get statefulsets -n otto -o name)
    for statefulset in $statefulsets; do
        local ready=$(kubectl get $statefulset -n otto -o jsonpath='{.status.readyReplicas}')
        local desired=$(kubectl get $statefulset -n otto -o jsonpath='{.spec.replicas}')
        if [[ "$ready" != "$desired" ]]; then
            return 1
        fi
    done
    return 0
}

# Wait for both deployments and statefulsets to be ready
echo "Waiting for deployments and statefulsets to be ready..."
while ! (check_deployments_ready && check_statefulsets_ready); do
    echo "Not all deployments and statefulsets are ready yet. Waiting for 10 seconds..."
    sleep 10
done

echo 
echo "All deployments and statefulsets are ready!"
echo
echo "The site URL is: $SITE_URL"
echo 

# TODO: Uncomment Velero after the change request is approved
# # Run the Velero setup script
# source check_velero.sh

cd /home/azureuser/otto/setup
