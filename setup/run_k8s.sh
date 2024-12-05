#!/bin/bash

# CM-8 & CM-9: Automate the deployment process, ensuring the inventory remains current and consistent

# Ensure we're in the correct directory
cd /home/azureuser/otto/setup/k8s

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

export OTTO_IDENTITY_ID=$(az identity show \
  --name otto-identity \
  --resource-group $RESOURCE_GROUP_NAME \
  --query clientId \
  --output tsv)

export KEYVAULT_CSI_PROVIDER_CLIENT_ID=$(az aks show \
  --resource-group OttoDEVRg \
  --name jus-dev-otto-aks \
  --query "addonProfiles.azureKeyvaultSecretsProvider.identity.clientId" \
  --output tsv) 
  

# Apply the NGINX Ingress Controller
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.2/deploy/static/provider/cloud/deploy.yaml
kubectl patch configmap ingress-nginx-controller -n ingress-nginx --type=merge -p '{"data":{"use-gzip":"true", "gzip-min-length":"1024"}}'

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
kubectl apply -f letsencrypt-cluster-issuer.yaml

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
# Do not use envsubst on these files as they contain variables in the script
kubectl apply -f db-backups.yaml
kubectl apply -f media-sync.yaml

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

echo "All deployments and statefulsets are ready!"


# Prompt the user if they want to run the initial setup
# If the INIT_SCRIPT is not set, prompt the user
if [[ -z "$INIT_SCRIPT" ]]; then
    read -p "Do you want to run the initial setup? (y/N): " INIT_SCRIPT
fi

# If initial setup is confirmed, run the initial setup script
if [[ $INIT_SCRIPT =~ ^[Yy]$ ]]; then
    export COORDINATOR_POD=$(kubectl get pods -n otto -l app=django-app -o jsonpath='{.items[0].metadata.name}')
    kubectl exec -it $COORDINATOR_POD -n otto -- env OTTO_ADMIN="${OTTO_ADMIN}" /django/initial_setup.sh
fi


# # If the DNS_LABEL is set, update the DNS label for the public IP. This is only necessary if not using a custom domain.
# if [ -n "$DNS_LABEL" ]; then

#     # If SET_DNS_LABEL is not set, prompt the user
#     if [[ -z "$SET_DNS_LABEL" ]]; then
#         read -p "Do you want to set the DNS label for the public IP to ${DNS_LABEL}? (y/N): " SET_DNS_LABEL
#     fi

#     # If the user confirms, proceed with setting the DNS label
#     if [[ $SET_DNS_LABEL =~ ^[Yy]$ ]]; then

#         # Get the AKS cluster managed resource group
#         export MC_RESOURCE_GROUP=$(az aks show --resource-group $RESOURCE_GROUP_NAME --name $AKS_CLUSTER_NAME --query nodeResourceGroup -o tsv)

#         # Find the LoadBalancer service and capture the external IP
#         EXTERNAL_IP=$(kubectl get svc -A -o jsonpath='{.items[?(@.spec.type=="LoadBalancer")].status.loadBalancer.ingress[0].ip}')
#         echo "Load Balancer External IP: $EXTERNAL_IP"

#         # Replace <your-external-ip> with the actual external IP you captured
#         PUBLIC_IP_RESOURCE_ID=$(az network public-ip list --resource-group $MC_RESOURCE_GROUP --query "[?ipAddress=='$EXTERNAL_IP'].id" -o tsv)
#         echo "Public IP Resource ID: $PUBLIC_IP_RESOURCE_ID"

#         # Replace <public-ip-resource-id> with the actual ID you obtained
#         az network public-ip update \
#             --ids $PUBLIC_IP_RESOURCE_ID \
#             --dns-name ${DNS_LABEL} > /dev/null

#         # Inform the user that the DNS label has been set and that it can take a few minutes to propagate
#         echo "The DNS label has been set. Once propagation completes in a few minutes, you can access the site."

#     fi

# else

#     # If the DNS_LABEL is not set, inform the user to update the DNS entries manually
#     echo "Please ensure the DNS entries point to the external IP of the Load Balancer."
#     echo "The external IP of the Load Balancer is: $EXTERNAL_IP"

# fi

echo "The site URL is: $SITE_URL"

# TODO: Uncomment Velero after the change request is approved
# # Run the Velero setup script
# source check_velero.sh
