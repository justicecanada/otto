param (
    [string]$subscription = "OttoDev",
    [string]$env_file = ".env.dev"
)


# Import variables from .env file
$envVariables = @{}
Get-Content $env_file | ForEach-Object {
    # Skip empty lines and lines starting with #
    if (-not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith("#")) {
        if ($_ -match '^\s*(.+?)\s*=\s*(.+?)\s*$') {
            $key = $matches[1].Trim()
            $value = $matches[2] -replace '^\s*"|"\s*$', '' -replace '^\s*''|''\s*$', ''
            $envVariables[$key] = $value.Trim()
            Set-Variable -Name $key -Value $value.Trim()
        }
    }
}


# Ensure Azure login and correct subscription selection
az login
if ($subscription -eq "") {
    Write-Host "Available subscriptions:"
    az account list --query "[].{SubscriptionId:id, Name:name}" --output table
    $SUBSCRIPTION_ID = Read-Host -Prompt "Enter the Subscription ID you want to use"
}
else {
    # Look up the ID of the subscription name provided
    $SUBSCRIPTION_ID = az account list --query "[?name=='$subscription'].id" -o tsv
    Write-Host "Using subscription ID: $SUBSCRIPTION_ID"
}
az account set --subscription $SUBSCRIPTION_ID


# Create management resource group if it doesn't exist
$MGMT_RESOURCE_GROUP_NAME = "${APP_NAME}$($INTENDED_USE.ToUpper())MgmtRg"
$groupExists = az group show --name $MGMT_RESOURCE_GROUP_NAME --only-show-errors 2>$null
if (-not $groupExists) {
    Write-Host "Creating resource group: $MGMT_RESOURCE_GROUP_NAME"
    
    az group create `
        --name "$MGMT_RESOURCE_GROUP_NAME" `
        --location "$LOCATION" `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors

}
else {
    Write-Host "Resource group $MGMT_RESOURCE_GROUP_NAME already exists"
}


# Check if VNet exists. If not, create it.
$vnet_exists = az network vnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $VNET_NAME --only-show-errors 2>$null
if (-not $vnet_exists) {
    Write-Host "Creating VNet: $VNET_NAME"

    # Set DNS servers
    az network vnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $VNET_NAME `
        --address-prefix $VNET_IP_RANGE `
        --location $LOCATION `
        --dns-servers 10.250.255.4 10.250.255.5 8.8.8.8 8.8.4.4 `
        --only-show-errors
}
else {
    Write-Host "VNet $VNET_NAME already exists"
}


# Check if the Bastion subnet exists. If not, create it.
$bastion_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $BASTION_SUBNET_NAME --only-show-errors 2>$null
if (-not $bastion_subnet_exists) {
    Write-Host "Creating Bastion subnet: $BASTION_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $BASTION_SUBNET_NAME `
        --address-prefix $BASTION_SUBNET_IP_RANGE `
        --only-show-errors
}
else {
    Write-Host "Bastion subnet $BASTION_SUBNET_NAME already exists"
}


# Check if App subnet exists. If not, create it.
$app_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $APP_SUBNET_NAME --only-show-errors 2>$null
if (-not $app_subnet_exists) {
    Write-Host "Creating App subnet: $APP_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $APP_SUBNET_NAME `
        --address-prefix $APP_SUBNET_IP_RANGE `
        --disable-private-endpoint-network-policies true `
        --disable-private-link-service-network-policies false `
        --only-show-errors
}
else {
    Write-Host "App subnet $APP_SUBNET_NAME already exists"
}


# Check if the Web subnet exists. If not, create it.
$web_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $WEB_SUBNET_NAME --only-show-errors 2>$null
if (-not $web_subnet_exists) {
    Write-Host "Creating Web subnet: $WEB_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $WEB_SUBNET_NAME `
        --address-prefix $WEB_SUBNET_IP_RANGE `
        --disable-private-endpoint-network-policies true `
        --disable-private-link-service-network-policies false `
        --only-show-errors
}
else {
    Write-Host "Web subnet $WEB_SUBNET_NAME already exists"
}


# Check if the Bastion public IP exists. If not, create it.
$bastion_pip_exists = az network public-ip show --resource-group $MGMT_RESOURCE_GROUP_NAME --name bastion-pip --only-show-errors 2>$null
if (-not $bastion_pip_exists) {
    Write-Host "Creating Bastion public IP"
    az network public-ip create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name bastion-pip `
        --sku Standard `
        --allocation-method Static `
        --idle-timeout 4 `
        --zone 1 `
        --location $LOCATION `
        --only-show-errors
}
else {
    Write-Host "Bastion public IP already exists"
}


# Check if the Bastion exists. If not, create it.
$bastionExists = az network bastion show --resource-group $MGMT_RESOURCE_GROUP_NAME --name bastion --only-show-errors 2>$null
if (-not $bastionExists) {
    Write-Host "Creating Bastion service"
    
    # Enable dynamic install for Bastion
    az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors

    az network bastion create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name bastion `
        --public-ip-address bastion-pip `
        --vnet-name $VNET_NAME `
        --sku Standard `
        --enable-tunneling true `
        --location $LOCATION `
        --only-show-errors
}
else {
    Write-Host "Bastion service already exists"
}


$JUMPBOX_VM_NAME = "jumpbox"

# Check if the Jumpbox VM exists. If not, create it.
$jumpboxExists = az vm show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_VM_NAME --only-show-errors 2>$null
if (-not $jumpboxExists) {
    Write-Host "Creating Jumpbox VM: $JUMPBOX_VM_NAME"
    az vm create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $JUMPBOX_VM_NAME `
        --image Ubuntu2204 `
        --size Standard_B2s `
        --vnet-name $VNET_NAME `
        --subnet $APP_SUBNET_NAME `
        --admin-username azureuser `
        --generate-ssh-keys `
        --public-ip-address '""' `
        --nsg-rule NONE `
        --only-show-errors
}
else {
    Write-Host "Jumpbox VM $JUMPBOX_VM_NAME already exists"
}


# Check if the Jumpbox VM has a managed identity. If not, assign one and capture the VM_IDENTITY_ID.
$vmIdentityExists = az vm identity show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_VM_NAME --only-show-errors 2>$null
if (-not $vmIdentityExists) {
    Write-Host "Assigning managed identity to Jumpbox VM"
    az vm identity assign --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_VM_NAME --only-show-errors
}
else {
    Write-Host "Jumpbox VM already has a managed identity"
}


# Capture the VM identity ID
$VM_IDENTITY_ID = az vm identity show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_VM_NAME --query principalId -o tsv

# Check if the VM identity has Contributor role assignment. If not, assign it.
$roleAssignment = az role assignment list --assignee $VM_IDENTITY_ID --role Contributor --scope /subscriptions/$SUBSCRIPTION_ID -o tsv
if (-not $roleAssignment) {
    Write-Host "Assigning Contributor role to VM identity"

    # Assign the Contributor role to the VM identity
    az role assignment create `
        --assignee $VM_IDENTITY_ID `
        --role Contributor `
        --scope /subscriptions/$SUBSCRIPTION_ID `
        --only-show-errors
}
else {
    Write-Host "VM identity already has the Contributor role assignment"
}


$MGMT_STORAGE_NAME = "${ORGANIZATION}${INTENDED_USE}${APP_NAME}mgmt".ToLower()

# Check if a storage account with the prefix already exists
$existing_account = az storage account list --resource-group $MGMT_RESOURCE_GROUP_NAME --query "[?starts_with(name, '${MGMT_STORAGE_NAME}')].name" -o tsv

if ($existing_account) {
    $MGMT_STORAGE_NAME = $existing_account
    Write-Host "Using existing storage account: $MGMT_STORAGE_NAME"
}
else {
    # Generate a random 5-digit alphanumeric suffix
    $random_suffix = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 5 | ForEach-Object { [char]$_ })

    # Create the storage account name
    $MGMT_STORAGE_NAME = "${MGMT_STORAGE_NAME}$random_suffix".ToLower()

    # Create storage account
    Write-Host "Creating storage account: $MGMT_STORAGE_NAME"
    az storage account create `
        --name $MGMT_STORAGE_NAME `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --location $LOCATION `
        --sku Standard_LRS `
        --kind StorageV2 `
        --encryption-services blob `
        --min-tls-version TLS1_2 `
        --allow-blob-public-access false `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors
}


$TF_STATE_CONTAINER = "tfstate"

# Check if the blob container exists
$container_exists = az storage container show `
    --name $TF_STATE_CONTAINER `
    --account-name $MGMT_STORAGE_NAME `
    --auth-mode login `
    --only-show-errors 2>$null

if (-not $container_exists) {
    Write-Host "Creating blob container: $TF_STATE_CONTAINER"
    az storage container create `
        --name $TF_STATE_CONTAINER `
        --account-name $MGMT_STORAGE_NAME `
        --auth-mode login `
        --only-show-errors
}
else {
    Write-Host "Blob container $TF_STATE_CONTAINER already exists"
}


# If the current user doesn't have an SSH key, generate one
if (-not (Test-Path "~/.ssh/id_rsa")) {
    Write-Host "Generating SSH key"
    ssh-keygen -t rsa -b 4096 -N '""' -f ~/.ssh/id_rsa
}

# Update the Jumpbox VM with the SSH key
Write-Host "Updating the Jumpbox VM with the SSH key"
$sshPublicKey = Get-Content "~/.ssh/id_rsa.pub" -Raw
az vm user update `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name $JUMPBOX_VM_NAME `
    --username azureuser `
    --ssh-key-value $sshPublicKey `
    --only-show-errors


    
# Add script-defined variables to the hashtable
$scriptVariables = @{
    'SUBSCRIPTION_ID'          = $SUBSCRIPTION_ID
    'MGMT_RESOURCE_GROUP_NAME' = $MGMT_RESOURCE_GROUP_NAME
    'MGMT_STORAGE_NAME'        = $MGMT_STORAGE_NAME
    'TF_STATE_CONTAINER'       = $TF_STATE_CONTAINER
    'JUMPBOX_VM_NAME'          = $JUMPBOX_VM_NAME
}

# Merge the two hashtables
$allVariables = $envVariables + $scriptVariables

# Generate the content for the new .env file with line breaks
$envContent = ($allVariables.GetEnumerator() | ForEach-Object {
        "$($_.Key)=$($_.Value)"
    }) -join "`n"

# Upload the .env file to the VM
Write-Host "Uploading .env file to the VM..."
az vm run-command invoke `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name $JUMPBOX_VM_NAME `
    --command-id RunShellScript `
    --scripts "echo '$envContent' > /home/azureuser/.env"


# Get the VM ID
$VM_ID = az vm show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_VM_NAME --query id -o tsv

# Connect using Azure CLI
Write-Host "Connecting to the Jumpbox VM"
az network bastion ssh `
    --name bastion `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --target-resource-id $VM_ID `
    --auth-type ssh-key `
    --username azureuser `
    --ssh-key "~/.ssh/id_rsa"
