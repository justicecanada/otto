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

# Login to ACR
az acr login --name $ACR_NAME

# Push Docker image to ACR
docker push ${IMAGE_NAME}:${SPECIFIC_TAG}
docker push ${IMAGE_NAME}:latest
