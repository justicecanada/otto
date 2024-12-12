#!/bin/bash

# Store the current directory
CURRENT_DIR=$(pwd)

# When the script exists, go back to the original directory
trap 'cd $CURRENT_DIR' EXIT

# Ensure we're in the correct directory
cd /home/azureuser/otto/django

# Get the latest Git commit hash
GITHUB_HASH=$(git rev-parse HEAD)

# Create version.yaml content
cat << EOF > version.yaml
github_hash: $GITHUB_HASH
build_date: $(date -u +"%Y-%m-%d %H:%M:%S")
EOF

# Prepare image name and tag
IMAGE_NAME="$ACR_NAME.azurecr.io/otto"
SPECIFIC_TAG=$GITHUB_HASH

# Build Docker image
docker build -t ${IMAGE_NAME}:${SPECIFIC_TAG} -f Dockerfile .

# Tag Docker image for ACR
docker tag ${IMAGE_NAME}:${SPECIFIC_TAG} ${IMAGE_NAME}:latest

# Push Docker image to ACR
# Docker doesn't automatically use Azure's managed identity authentication. To 
# resolve this, we can use the Azure CLI to obtain an access token for the ACR and 
# then use that token with Docker. This script will log in to the ACR, push the
# images, and then log out.
push_to_acr() {

    local acr_name=$1
    local image_name=$2
    local specific_tag=$3

    # Get ACR login server
    local acr_login_server=$(az acr show --name "$acr_name" --query loginServer --output tsv)

    # Get access token for ACR (suppress output to avoid exposing it)
    local acr_access_token=$(az acr login --name "$acr_name" --expose-token --query accessToken --output tsv 2>/dev/null)

    # Log in to Docker with the access token (suppress output)
    echo "$acr_access_token" | docker login "$acr_login_server" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null 2>&1

    # Push Docker images
    docker push "${image_name}:${specific_tag}"
    docker push "${image_name}:latest"

    # Log out from Docker
    docker logout "$acr_login_server" >/dev/null 2>&1

    # Clear sensitive variables
    unset acr_access_token
}

# Usage
push_to_acr "$ACR_NAME" "$IMAGE_NAME" "$SPECIFIC_TAG"
