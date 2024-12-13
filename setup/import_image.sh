#!/bin/bash

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

fetch_and_push_to_acr "postgres:16" "$ACR_NAME"
fetch_and_push_to_acr "pgvector/pgvector:pg16" "$ACR_NAME"
