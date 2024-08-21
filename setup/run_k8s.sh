#!/bin/bash

source setup_env.sh

# Check if the image exists
export IMAGE_EXISTS=$(
    az acr repository show-tags \
        --name $ACR_NAME \
        --repository otto \
        --output tsv | grep -q "^latest$" && echo true || echo false
    )

if [[ $IMAGE_EXISTS == "false" ]]; then
    echo "The latest image does not exist in the ACR. Exiting..."
    return 1
fi

# Get the credentials for the AKS cluster and exit if it fails
if ! az aks get-credentials --resource-group "$RESOURCE_GROUP_NAME" --name "$AKS_CLUSTER_NAME" --overwrite-existing; then
    echo "Failed to get AKS credentials. Exiting..."
    return 1
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
kubectl apply -f /workspace/k8s/namespace.yaml

# Apply the Cluster Issuer for Let's Encrypt which will automatically provision certificates for the Ingress resources
kubectl apply -f /workspace/k8s/letsencrypt-cluster-issuer.yaml

# Apply the Kubernetes resources related to Otto, substituting environment variables where required
envsubst < /workspace/k8s/ingress.yaml | kubectl apply -f -
envsubst < /workspace/k8s/configmap.yaml | kubectl apply -f -
envsubst < /workspace/k8s/secrets.yaml | kubectl apply -f -
envsubst < /workspace/k8s/storageclass.yaml | kubectl apply -f -
envsubst < /workspace/k8s/vectordb.yaml | kubectl apply -f -
envsubst < /workspace/k8s/django.yaml | kubectl apply -f -
envsubst < /workspace/k8s/redis.yaml | kubectl apply -f -
envsubst < /workspace/k8s/celery.yaml | kubectl apply -f -

# Function to check if all pods are ready
check_pods_ready() {
    local total_pods=$(kubectl get pods -n otto --no-headers | wc -l)
    local ready_pods=$(kubectl get pods -n otto -o jsonpath='{.items[*].status.containerStatuses[*].ready}' | tr ' ' '\n' | grep -c true)
    return $(( total_pods == ready_pods || total_pods == 0 ))
}

# Wait for the pods to be ready
echo "Waiting for pods to be ready..."
while check_pods_ready; do
    echo "Not all pods are ready yet. Waiting for 10 seconds..."
    sleep 10
done

# Prompt the user if they want to run the initial setup
read -p "Pods are ready. Do you want to run the initial setup? (y/N) " -e -r

# If yes, run the initial setup
if [[ $REPLY =~ ^[Yy]$ ]]; then
    export COORDINATOR_POD=$(kubectl get pods -n otto -l app=django-app -o jsonpath='{.items[0].metadata.name}')
    kubectl exec -it $COORDINATOR_POD -n otto -- /otto/initial_setup.sh
fi



# At the time of writing (Aug 2024), the `dns_prefix` attribute in the `azurerm_kubernetes_cluster` 
# terraform resource doesn't directly set the DNS name label on the public IP created for the AKS 
# cluster. This extra step is necessary because Terraform doesn't currently have a built-in way to 
# manage this new Azure feature. However, it's important to note that this situation is likely to 
# change in the future as Azure and Terraform providers adapt to these new security measures.

# Prompt the user if they want to update the DNS label
read -p "Do you want to set the DNS label to ${HOST_NAME_PREFIX}? (y/N) " -e -r

# If yes, update the DNS label
if [[ $REPLY =~ ^[Yy]$ ]]; then
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
        --dns-name ${HOST_NAME_PREFIX}

    # Wait for HTTPS site to be accessible
    echo "Waiting for HTTPS site to be accessible... (This can take a few minutes)"
    FQDN="https://${HOST_NAME_PREFIX}.${LOCATION}.cloudapp.azure.com"
    MAX_RETRIES=30
    RETRY_INTERVAL=10

    for ((i=1; i<=MAX_RETRIES; i++)); do
        if curl -s -o /dev/null -w "%{http_code}" "$FQDN" | grep -q "200\|301\|302"; then
            echo "HTTPS site is accessible! URL: $FQDN"
            break
        fi
        echo "Attempt $i: HTTPS site not yet accessible. Waiting $RETRY_INTERVAL seconds..."
        sleep $RETRY_INTERVAL
    done

    if [[ $i -gt $MAX_RETRIES ]]; then
        echo "HTTPS site accessibility check timed out. Please check your configuration and try again."
    fi
    
fi
