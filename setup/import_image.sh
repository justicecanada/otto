#!/bin/bash

# Build the otto image
build_otto_image() {
    local current_dir=$(pwd)
    local django_dir="/home/azureuser/otto/django"
    local acr_name=$1
    local image_name="${acr_name}.azurecr.io/otto"

    # Change to the Django directory
    cd "$django_dir"

    # Get the latest Git commit hash
    local github_hash=$(git rev-parse HEAD)

    # Create version.yaml content
    cat << EOF > version.yaml
github_hash: $github_hash
build_date: $(date -u +"%Y-%m-%d %H:%M:%S")
EOF

    # Build Docker image
    docker build -t ${image_name}:${github_hash} -f Dockerfile .

    # Tag Docker image for ACR
    docker tag ${image_name}:${github_hash} ${image_name}:latest

    # Return to the original directory
    cd "$current_dir"

    # Return the image name and tag
    echo "${image_name} ${github_hash}"
}

# Push the image to ACR
push_to_acr() {

    local image_name=$1
    local acr_name=$2

    # Get ACR login server
    local acr_login_server=$(az acr show --name "$acr_name" --query loginServer --output tsv)

    # Get access token for ACR (suppress output to avoid exposing it)
    local acr_access_token=$(az acr login --name "$acr_name" --expose-token --query accessToken --output tsv 2>/dev/null)

    # Log in to Docker with the access token (suppress output)
    echo "$acr_access_token" | docker login "$acr_login_server" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null 2>&1

    # Push Docker image
    docker push "${image_name}"

    # Log out from Docker
    docker logout "$acr_login_server" >/dev/null 2>&1

    # Clear sensitive variables
    unset acr_access_token
}

fetch_and_push_to_acr() {
    local image_name=$1
    local acr_name=$2

    # Pull the image
    docker pull "${image_name}"

    # Tag the image for ACR
    docker tag "${image_name}" "${acr_name}.azurecr.io/${image_name}"

    # Push the image to ACR
    push_to_acr "${acr_name}.azurecr.io/${image_name}" "$acr_name"
}

# Build the otto image
read image_name github_hash <<< $(build_otto_image "$ACR_NAME")

# Push the otto image
push_to_acr "$image_name:$github_hash" "$ACR_NAME" 
push_to_acr "$image_name:latest" "$ACR_NAME"

# Fetch and push the other required images
fetch_and_push_to_acr "postgres:16" "$ACR_NAME"
fetch_and_push_to_acr "pgvector/pgvector:pg16" "$ACR_NAME"
fetch_and_push_to_acr "redis:7.0.11-bullseye" "$ACR_NAME"
fetch_and_push_to_acr "registry.k8s.io/ingress-nginx/controller:v1.8.1" "$ACR_NAME"
fetch_and_push_to_acr "registry.k8s.io/ingress-nginx/kube-webhook-certgen:v20230407" "$ACR_NAME"
fetch_and_push_to_acr "registry.k8s.io/ingress-nginx/custom-error-pages:v1.0.2" "$ACR_NAME"
