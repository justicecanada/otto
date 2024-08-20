#!/bin/bash

source setup_env.sh

export WORKSPACE_NAME="${SUBSCRIPTION_NAME}-${RESOURCE_GROUP_NAME}"

# Check if the workspace already exists
if ! terraform -chdir=./terraform workspace list | grep -q "$WORKSPACE_NAME"; then
    terraform -chdir=./terraform workspace new $WORKSPACE_NAME
fi

# Select the new workspace
terraform -chdir=./terraform workspace select $WORKSPACE_NAME

# Ensure terraform is initialied and upgraded
terraform -chdir=./terraform init -upgrade

# Apply the Terraform configuration
terraform -chdir=./terraform apply -var-file=.tfvars


