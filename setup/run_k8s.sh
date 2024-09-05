#!/bin/bash

source setup_env.sh

cd k8s

# Check if the image exists
export IMAGE_EXISTS=$(
    az acr repository show-tags \
        --name $ACR_NAME \
        --repository otto \
        --output tsv | grep -q "^latest$" && echo true || echo false
    )

if [[ $IMAGE_EXISTS == "false" ]]; then
    echo "The latest image does not exist in the ACR. Exiting..."
    exit 0
fi

# Get the credentials for the AKS cluster and exit if it fails
if ! az aks get-credentials --resource-group "$RESOURCE_GROUP_NAME" --name "$AKS_CLUSTER_NAME" --overwrite-existing; then
    echo "Failed to get AKS credentials. Exiting..."
    exit 0
fi

# Convert the kubeconfig to use the Azure CLI login mode, which utilizes the already logged-in context from Azure CLI to obtain the access token
kubelogin convert-kubeconfig -l azurecli

export AKS_IDENTITY_ID=$(
  az aks show \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --name "$AKS_CLUSTER_NAME" \
    --query addonProfiles.azureKeyvaultSecretsProvider.identity.clientId \
    -o tsv
)

# Apply the NGINX Ingress Controller and patch the service to use the public IP address and DNS label
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.2/deploy/static/provider/cloud/deploy.yaml

# Apply the Cert-Manager CRDs and Cert-Manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.crds.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.yaml

# Wait for cert-manager webhook to be ready
echo "Waiting for cert-manager webhook to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager

# Wait for the NGINX Ingress Controller to be ready
echo "Waiting for NGINX Ingress Controller to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/ingress-nginx-controller -n ingress-nginx

# Apply the namespace for Otto
kubectl apply -f namespace.yaml

# Apply the Cluster Issuer for Let's Encrypt which will automatically provision certificates for the Ingress resources
kubectl apply -f letsencrypt-cluster-issuer.yaml

# Apply the Kubernetes resources related to Otto, substituting environment variables where required
envsubst < ingress.yaml | kubectl apply -f -
envsubst < configmap.yaml | kubectl apply -f -
envsubst < secrets.yaml | kubectl apply -f -
envsubst < storageclass.yaml | kubectl apply -f -
envsubst < vectordb.yaml | kubectl apply -f -
envsubst < django.yaml | kubectl apply -f -
envsubst < redis.yaml | kubectl apply -f -
envsubst < celery.yaml | kubectl apply -f -

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

# Wait for the deployments (except those containing "celery") to be ready
echo "Waiting for deployments to be ready..."
while ! check_deployments_ready; do
    echo "Not all deployments are ready yet. Waiting for 10 seconds..."
    sleep 10
done

echo "All deployments are ready."

# Prompt the user if they want to run the initial setup
read -p "Do you want to run the initial setup? (y/N) " -e -r

# If yes, run the initial setup
if [[ $REPLY =~ ^[Yy]$ ]]; then
    export COORDINATOR_POD=$(kubectl get pods -n otto -l app=django-app -o jsonpath='{.items[0].metadata.name}')
    kubectl exec -it $COORDINATOR_POD -n otto -- env OTTO_ADMIN="${OTTO_ADMIN}" /otto/initial_setup.sh
fi


# If the DNS_LABEL is set, update the DNS label for the public IP. This is only necessary if not using a custom domain.
if [ -n "$DNS_LABEL" ]; then
    echo "DNS label is set to ${DNS_LABEL}. Proceeding with DNS label update."

    # Get the AKS cluster managed resource group
    export MC_RESOURCE_GROUP=$(az aks show --resource-group $RESOURCE_GROUP_NAME --name $AKS_CLUSTER_NAME --query nodeResourceGroup -o tsv)

    # Find the LoadBalancer service and capture the external IP
    EXTERNAL_IP=$(kubectl get svc -A -o jsonpath='{.items[?(@.spec.type=="LoadBalancer")].status.loadBalancer.ingress[0].ip}')
    echo "Load Balancer External IP: $EXTERNAL_IP"

    # Replace <your-external-ip> with the actual external IP you captured
    PUBLIC_IP_RESOURCE_ID=$(az network public-ip list --resource-group $MC_RESOURCE_GROUP --query "[?ipAddress=='$EXTERNAL_IP'].id" -o tsv)
    echo "Public IP Resource ID: $PUBLIC_IP_RESOURCE_ID"

    # Replace <public-ip-resource-id> with the actual ID you obtained
    az network public-ip update \
        --ids $PUBLIC_IP_RESOURCE_ID \
        --dns-name ${DNS_LABEL}

    # Inform the user that the DNS label has been set and that it can take a few minutes to propagate
    echo "The DNS label has been set. Once propagation completes in a few minutes, you can access the site at $SITE_URL."
    
else

    # If the DNS_LABEL is not set, inform the user to update the DNS entries manually
    echo "Please update the DNS entries to point to the external IP of the Load Balancer."
    echo "The external IP of the Load Balancer is: $EXTERNAL_IP"
    echo "The site URL is: $SITE_URL"

fi
