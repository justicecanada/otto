param (
    [string]$subscription = "",
    [string]$envFile = ""
)


# Ensure Azure login and correct subscription selection
$loggedIn = az account show --only-show-errors --output none
if ($loggedIn -eq "") {
    az login --only-show-errors --output none
}

if ($subscription -eq "") {
    Write-Host "Available subscriptions:"
    az account list --query "[].{SubscriptionId:id, Name:name}" --output table
    $SUBSCRIPTION_ID = Read-Host -Prompt "Enter the Subscription ID to use"
}
else {
    # Look up the ID of the subscription name provided
    $SUBSCRIPTION_ID = az account list --query "[?name=='$subscription'].id" -o tsv
    Write-Host "Using subscription ID: $SUBSCRIPTION_ID"
}
az account set --subscription $SUBSCRIPTION_ID `
    --only-show-errors `
    --output none


# Get the logged-in user's name
$loggedInUser = az account show --query user.name -o tsv

# Check for Owner role assignment
$ownerRoleAssignment = az role assignment list --assignee $loggedInUser --role "Owner" --scope /subscriptions/$SUBSCRIPTION_ID -o tsv

# If the logged-in user is not an Owner, prompt them to activate the role before continuing
if (-not $ownerRoleAssignment) {
    Write-Host "The Owner role, with highest privilege, is required for the subscription. The Contributor role is not sufficient as it lacks User Access Administrator permissions."
    Write-Host "Please activate the role or contact an Administrator to assign the Owner role to $loggedInUser."
    
    $continue = Read-Host "Have you been assigned the Owner role and has it been activated? (y/N)"
    
    if ($continue.ToLower() -ne "y") {
        Write-Host "Exiting script. Please run the script again once you have been assigned the Owner role."
        exit
    }
}



# If $envFile is not provided, prompt for it by listing the .env* files in the current directory
if (-not $envFile) {
    Write-Host "Available .env files in the current directory:"
    Get-ChildItem -Filter ".env*" | ForEach-Object { $_.Name }
    $envFile = Read-Host -Prompt "Specify the .env file to use"
}

# Check if the .env file exists
if (-not (Test-Path $envFile)) {
    Write-Host "The specified .env file does not exist: $envFile"
    exit
}

# Read the .env file and set the environment variables
Get-Content $envFile | ForEach-Object {
    # Skip empty lines and lines starting with #
    if (-not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith("#")) {
        if ($_ -match '^\s*(.+?)\s*=\s*(.+?)\s*$') {
            $key = $matches[1].Trim()
            $value = $matches[2] -replace '^\s*"|"\s*$', '' -replace '^\s*''|''\s*$', ''
            Set-Variable -Name $key -Value $value.Trim()
        }
    }
}


# Function to get group IDs from group names
function Get-GroupIds {
    param (
        [string]$groupNames
    )
    
    $groupIds = @()
    $namesArray = $groupNames -split ','

    foreach ($name in $namesArray) {
        $trimmedName = $name.Trim()
        Write-Host "Looking up group: $trimmedName"
        $groupId = az ad group show --group "$trimmedName" --query id -o tsv
        if ($groupId) {
            $groupIds += $groupId
        }
        else {
            Write-Host "Group not found: $trimmedName"
        }
    }

    return ($groupIds -join ',')
}

# Set script variables
$ADMIN_GROUP_ID = Get-GroupIds -groupNames $ADMIN_GROUP_NAME
$LOG_ANALYTICS_READERS_GROUP_ID = Get-GroupIds -groupNames $LOG_ANALYTICS_READERS_GROUP_NAME
$ENTRA_CLIENT_ID = az ad app list --display-name "${ENTRA_CLIENT_NAME}" --query "[].{appId:appId}" --output tsv

$MGMT_RESOURCE_GROUP_NAME = "${APP_NAME}$($INTENDED_USE.ToUpper())MgmtRg"
$MGMT_STORAGE_NAME = "${ORGANIZATION}${INTENDED_USE}${APP_NAME}mgmt".ToLower()
$ACR_NAME = "${ORGANIZATION}${INTENDED_USE}${APP_NAME}acr".ToLower()
$KEYVAULT_NAME = "${ORGANIZATION}-${INTENDED_USE}-${APP_NAME}-kv".ToLower()
$JUMPBOX_NAME = "jumpbox"
$JUMPBOX_IDENTITY_NAME = "$JUMPBOX_NAME-identity"

# Create management resource group if it doesn't exist
$groupExists = az group show --name $MGMT_RESOURCE_GROUP_NAME --only-show-errors 2>$null
if (-not $groupExists) {
    Write-Host "Creating resource group: $MGMT_RESOURCE_GROUP_NAME"
    
    az group create `
        --name "$MGMT_RESOURCE_GROUP_NAME" `
        --location "$LOCATION" `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors `
        --output none

}
else {
    Write-Host "Resource group $MGMT_RESOURCE_GROUP_NAME already exists"
}


# Check if VNet exists. If not, create it.
$vnet_exists = az network vnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $VNET_NAME --only-show-errors 2>$null
if (-not $vnet_exists) {
    Write-Host "Creating VNet: $VNET_NAME"

    # Set DNS servers
    # TODO: Consider using custom DNS servers 10.250.255.4 10.250.255.5 8.8.8.8 8.8.4.4;
    # The Azure DNS 168.63.129.16 was required for the private endpoint to work.
    az network vnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $VNET_NAME `
        --address-prefix $VNET_IP_RANGE `
        --location $LOCATION `
        --dns-servers 168.63.129.16 `
        --only-show-errors `
        --output none
}
else {
    Write-Host "VNet $VNET_NAME already exists"
}
$vnetId = az network vnet show --name $VNET_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query id -o tsv


# Check if the Web subnet exists. If not, create it.
$web_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $WEB_SUBNET_NAME --only-show-errors 2>$null
if (-not $web_subnet_exists) {
    Write-Host "Creating subnet: $WEB_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $WEB_SUBNET_NAME `
        --address-prefix $WEB_SUBNET_IP_RANGE `
        --disable-private-endpoint-network-policies true `
        --disable-private-link-service-network-policies false `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Subnet $WEB_SUBNET_NAME already exists"
}
$webSubnetId = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $WEB_SUBNET_NAME --query id -o tsv


# Get the service endpoints for the Web subnet
$webServiceEndpoints = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $WEB_SUBNET_NAME --query "serviceEndpoints[].service" -o tsv

# Check if the Web subnet has service endpoint for KeyVault and Storage. If not, add them.
if ($webServiceEndpoints -notcontains "Microsoft.KeyVault" -or $webServiceEndpoints -notcontains "Microsoft.Storage") {
    Write-Host "Adding service endpoints to Web subnet"
    az network vnet subnet update `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $WEB_SUBNET_NAME `
        --service-endpoints Microsoft.KeyVault Microsoft.Storage `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Service endpoints already exists on Web subnet"
}


# Check if App subnet exists. If not, create it.
$app_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $APP_SUBNET_NAME --only-show-errors 2>$null
if (-not $app_subnet_exists) {
    Write-Host "Creating subnet: $APP_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $APP_SUBNET_NAME `
        --address-prefix $APP_SUBNET_IP_RANGE `
        --disable-private-endpoint-network-policies true `
        --disable-private-link-service-network-policies false `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Subnet $APP_SUBNET_NAME already exists"
}
$appSubnetId = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $APP_SUBNET_NAME --query id -o tsv

# Get the service endpoints for the App subnet
$appServiceEndpoints = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $APP_SUBNET_NAME --query "serviceEndpoints[].service" -o tsv

# Check if the App subnet has service endpoint for KeyVault and Storage. If not, add them.
if ($appServiceEndpoints -notcontains "Microsoft.KeyVault" -or $appServiceEndpoints -notcontains "Microsoft.Storage") {
    Write-Host "Adding service endpoints to App subnet"
    az network vnet subnet update `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $APP_SUBNET_NAME `
        --service-endpoints Microsoft.KeyVault Microsoft.Storage `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Service endpoints already exists on App subnet"
}


# Check if Mgmt subnet exists. If not, create it.
$mgmt_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $MGMT_SUBNET_NAME --only-show-errors 2>$null
if (-not $mgmt_subnet_exists) {
    Write-Host "Creating subnet: $MGMT_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $MGMT_SUBNET_NAME `
        --address-prefix $MGMT_SUBNET_IP_RANGE `
        --disable-private-endpoint-network-policies true `
        --disable-private-link-service-network-policies false `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Subnet $APP_SUBNET_NAME already exists"
}

# Get the service endpoints for the Mgmt subnet
$mgmtServiceEndpoints = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $MGMT_SUBNET_NAME --query "serviceEndpoints[].service" -o tsv

# Check if the Mgmt subnet has service endpoint for KeyVault and Storage. If not, add them.
if ($mgmtServiceEndpoints -notcontains "Microsoft.KeyVault" -or $mgmtServiceEndpoints -notcontains "Microsoft.Storage") {
    Write-Host "Adding service endpoints to Mgmt subnet"
    az network vnet subnet update `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --name $MGMT_SUBNET_NAME `
        --service-endpoints Microsoft.KeyVault Microsoft.Storage `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Service endpoints already exists on Mgmt subnet"
}


# Check if Bastion VNet exists. If not, create it.
$bastion_vnet_exists = az network vnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $BASTION_VNET_NAME --only-show-errors 2>$null
if (-not $bastion_vnet_exists) {
    Write-Host "Creating VNet: $BASTION_VNET_NAME"
    
    az network vnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $BASTION_VNET_NAME `
        --address-prefix $BASTION_VNET_IP_RANGE `
        --location $LOCATION `
        --dns-servers 10.250.255.4 10.250.255.5 `
        --only-show-errors `
        --output none
}
else {
    Write-Host "VNet $BASTION_VNET_NAME already exists"
}


# Check if the Bastion subnet exists in Bastion VNet. If not, create it.
$bastion_subnet_exists = az network vnet subnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --vnet-name $BASTION_VNET_NAME --name $BASTION_SUBNET_NAME --only-show-errors 2>$null
if (-not $bastion_subnet_exists) {
    Write-Host "Create subnet: $BASTION_SUBNET_NAME"
    az network vnet subnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $BASTION_VNET_NAME `
        --name $BASTION_SUBNET_NAME `
        --address-prefix $BASTION_SUBNET_IP_RANGE `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Subnet $BASTION_SUBNET_NAME already exists"
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
        --only-show-errors `
        --output none
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
        --vnet-name $BASTION_VNET_NAME `
        --sku Standard `
        --enable-tunneling true `
        --location $LOCATION `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Bastion service already exists"
}


$vnet1Id = az network vnet show --name $BASTION_VNET_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query id -o tsv
$vnet2Id = az network vnet show --name $VNET_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query id -o tsv

# Check if peering already exists from Bastion VNET to main VNET
$bastionToMainPeering = az network vnet peering show `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name "BastionToMain" `
    --vnet-name $BASTION_VNET_NAME `
    --only-show-errors 2>$null

if (-not $bastionToMainPeering) {
    Write-Host "Creating peering from Bastion VNET to main VNET"
    az network vnet peering create `
        --name "BastionToMain" `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $BASTION_VNET_NAME `
        --remote-vnet $vnet2Id `
        --allow-vnet-access `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Bastion to main VNET peering already exists"
}


# Check if peering already exists from main VNET to Bastion VNET
$mainToBastionPeering = az network vnet peering show `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name "MainToBastion" `
    --vnet-name $VNET_NAME `
    --only-show-errors 2>$null

if (-not $mainToBastionPeering) {
    Write-Host "Creating peering from main VNET to Bastion VNET"
    az network vnet peering create `
        --name "MainToBastion" `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --vnet-name $VNET_NAME `
        --remote-vnet $vnet1Id `
        --allow-vnet-access `
        --only-show-errors `
        --output none

}
else {
    Write-Host "Main to Bastion VNET peering already exists"
}


# Create NSG for Jumpbox
$nsgName = "$JUMPBOX_NAME-nsg"
$nsgExists = az network nsg show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $nsgName --only-show-errors 2>$null
if (-not $nsgExists) {
    Write-Host "Creating NSG: $nsgName"
    az network nsg create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $nsgName `
        --output none
}
else {
    Write-Host "NSG $nsgName already exists"
}


# Check if the NSG rule for SSH exists. If not, create it.
$sshRuleExists = az network nsg rule show --resource-group $MGMT_RESOURCE_GROUP_NAME --nsg-name $nsgName --name "AllowSSHFromBastion" --only-show-errors 2>$null
if (-not $sshRuleExists) {
    Write-Host "Creating NSG rule for SSH"
    az network nsg rule create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --nsg-name $nsgName `
        --name "AllowSSHFromBastion" `
        --priority 100 `
        --direction Inbound `
        --access Allow `
        --protocol Tcp `
        --source-address-prefix $BASTION_SUBNET_IP_RANGE `
        --source-port-range "*" `
        --destination-address-prefix "*" `
        --destination-port-range 22 `
        --output none
}
else {
    Write-Host "NSG rule for SSH already exists"
}


# Check if the user-assigned managed identity exists
$identityExists = az identity show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_IDENTITY_NAME --only-show-errors 2>$null

if (-not $identityExists) {
    Write-Host "Creating user-assigned managed identity: $JUMPBOX_IDENTITY_NAME"
    az identity create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $JUMPBOX_IDENTITY_NAME `
        --only-show-errors `
        --output none
}
else {
    Write-Host "User-assigned managed identity $JUMPBOX_IDENTITY_NAME already exists"
}


# Check if the Jumpbox NIC exists. If not, create it.
$jumpboxNicExists = az network nic show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_NAME-nic --only-show-errors 2>$null
if (-not $jumpboxNicExists) {
    Write-Host "Creating NIC for Jumpbox"
    az network nic create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $JUMPBOX_NAME-nic `
        --vnet-name $VNET_NAME `
        --subnet $MGMT_SUBNET_NAME `
        --private-ip-address $JUMPBOX_IP `
        --network-security-group $nsgName `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "NIC for Jumpbox already exists"
}


# Get the resource ID of the managed identity
$identityResourceId = az identity show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_IDENTITY_NAME --query id -o tsv
$identityId = az identity show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_IDENTITY_NAME --query principalId -o tsv

# Check if the identity has Owner role assignment. If not, attempt to assign it.
$ownerRoleAssignment = az role assignment list --assignee $identityId --role "Owner" --scope /subscriptions/$SUBSCRIPTION_ID -o tsv

if (-not $ownerRoleAssignment) {
    Write-Host "Identity does not have the Owner role assignment. Attempting to assign..."

    try {
        $result = az role assignment create `
            --assignee $identityId `
            --role "Owner" `
            --scope /subscriptions/$SUBSCRIPTION_ID `
            --only-show-errors `
            2>&1

        if ($LASTEXITCODE -ne 0) {
            throw $result
        }

        Write-Host "Successfully assigned Owner role to identity."
    }
    catch {
        Write-Host $_.Exception.Message
        Write-Host "The Owner role, with highest privilege, is required for the VM to create resources in the subscription and manage user access between resources. The Contributor role is not sufficient as it lacks User Access Administrator permissions. The Terraform script will fail without this role assignment. Please contact your Cloud Administrator or another subscription owner with highest privileges to assign the Owner role to $JUMPBOX_IDENTITY_NAME ($identityResourceId)."
        
        $continue = Read-Host "Do you want to continue without this role assignment? (y/n)"
        if ($continue -ne "y") {
            exit
        }
        Write-Host "Continuing with the rest of the script..."
    }
}
else {
    Write-Host "Identity already has the Owner role assignment."
}


# Check if the Jumpbox VM exists. If not, create it with the user-assigned managed identity.
$jumpboxExists = az vm show --resource-group $MGMT_RESOURCE_GROUP_NAME --name $JUMPBOX_NAME --only-show-errors 2>$null
if (-not $jumpboxExists) {
    Write-Host "Creating Jumpbox VM: $JUMPBOX_NAME"
    az vm create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $JUMPBOX_NAME `
        --image Ubuntu2204 `
        --size Standard_B2s `
        --nics $JUMPBOX_NAME-nic `
        --admin-username azureuser `
        --generate-ssh-keys `
        --assign-identity $identityResourceId `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Jumpbox VM $JUMPBOX_NAME already exists"
}


# Check if a storage account with the prefix already exists
$existing_account = az storage account list --resource-group $MGMT_RESOURCE_GROUP_NAME --query "[?starts_with(name, '${MGMT_STORAGE_NAME}')].name" -o tsv
if (-not $existing_account) {
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
        --public-network-access Disabled `
        --default-action Deny `
        --vnet-name $VNET_NAME `
        --subnet $MGMT_SUBNET_NAME `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Storage account with the prefix $MGMT_STORAGE_NAME already exists"
    $MGMT_STORAGE_NAME = $existing_account
}
# Get the resource ID of the storage account
$storageAccountId = az storage account show --name $MGMT_STORAGE_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --query id -o tsv


# Assign the "Storage Blob Data Contributor" role to the VM's managed identity for the storage account
$roleAssignment = az role assignment list --assignee $identityId --role "Storage Blob Data Contributor" --scope $storageAccountId -o tsv
if (-not $roleAssignment) {
    Write-Host "Assigning Storage Blob Data Contributor role to VM identity"

    # Assign the "Storage Blob Data Contributor" role to the VM identity
    az role assignment create `
        --assignee $identityId `
        --role "Storage Blob Data Contributor" `
        --scope $storageAccountId `
        --only-show-errors `
        --output none
}
else {
    Write-Host "VM identity already has the Storage Blob Data Contributor role assignment"
}


# Create the private endpoint for the storage account if it doesn't exist
$MGMT_STORAGE_ENDPOINT = "${MGMT_STORAGE_NAME}-endpoint"
$privateEndpointExists = az network private-endpoint show --resource-group $MGMT_RESOURCE_GROUP_NAME --name "$MGMT_STORAGE_ENDPOINT" --only-show-errors 2>$null
if (-not $privateEndpointExists) {
    Write-Host "Creating private endpoint for storage account"
    az network private-endpoint create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name "$MGMT_STORAGE_ENDPOINT" `
        --vnet-name $VNET_NAME `
        --subnet $MGMT_SUBNET_NAME `
        --private-connection-resource-id $storageAccountId `
        --group-id blob `
        --connection-name "$MGMT_STORAGE_NAME-connection" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Private endpoint for storage account already exists"
}


# Get the private endpoint network interface
$networkInterfaceId = az network private-endpoint show --resource-group $MGMT_RESOURCE_GROUP_NAME --name "$MGMT_STORAGE_ENDPOINT" --query "networkInterfaces[0].id" -o tsv

$networkInterfaceIpConfig = az resource show `
    --ids $networkInterfaceId `
    --api-version 2019-04-01 `
    --query 'properties.ipConfigurations[0].properties.privateIPAddress' `
    --output tsv


# Create the privatelink DNS zone for blob storage if it doesn't exist
$blobPrivateLinkDnsZoneExists = az network private-dns zone show --resource-group $MGMT_RESOURCE_GROUP_NAME --name "privatelink.blob.core.windows.net" --only-show-errors 2>$null
if (-not $blobPrivateLinkDnsZoneExists) {
    Write-Host "Creating privatelink DNS zone"
    az network private-dns zone create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name "privatelink.blob.core.windows.net" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Privatelink DNS zone for blob storage already exists"
}


# Link the privatelink DNS zone for blob storage to the VNet if it isn't already
$blobPrivateLinkDnsZoneLinked = az network private-dns link vnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --zone-name "privatelink.blob.core.windows.net" --name $VNET_NAME --only-show-errors 2>$null
if (-not $blobPrivateLinkDnsZoneLinked) {
    Write-Host "Linking privatelink DNS zone for blob storage to VNet"
    az network private-dns link vnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --zone-name "privatelink.blob.core.windows.net" `
        --name $VNET_NAME `
        --virtual-network $vnetId `
        --registration-enabled false `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Privatelink DNS zone for blob storage is already linked to VNet"
}


# Check if the A record for the storage account exists in the privatelink DNS zone for blob storage. If not, create it.
$blobRecordExists = az network private-dns record-set a show --resource-group $MGMT_RESOURCE_GROUP_NAME --zone-name "privatelink.blob.core.windows.net" --name $MGMT_STORAGE_NAME --only-show-errors 2>$null
if (-not $blobRecordExists) {
    Write-Host "Creating A record for storage account in privatelink DNS zone"
    az network private-dns record-set a add-record `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --zone-name "privatelink.blob.core.windows.net" `
        --record-set-name $MGMT_STORAGE_NAME `
        --ipv4-address $networkInterfaceIpConfig `
        --only-show-errors `
        --output none
}
else {
    Write-Host "A record for storage account already exists in privatelink DNS zone"
}


# Create the privatelink DNS zone for keyvault if it doesn't exist
$keyvaultPrivateLinkDnsZoneExists = az network private-dns zone show --resource-group $MGMT_RESOURCE_GROUP_NAME --name "privatelink.vaultcore.azure.net" --only-show-errors 2>$null
if (-not $keyvaultPrivateLinkDnsZoneExists) {
    Write-Host "Creating privatelink DNS zone for KeyVault"
    az network private-dns zone create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name "privatelink.vaultcore.azure.net" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Privatelink DNS zone for KeyVault already exists"
}

# Link the privatelink DNS zone for KeyVault to the VNet if it isn't already
$keyvaultPrivateLinkDnsZoneLinked = az network private-dns link vnet show --resource-group $MGMT_RESOURCE_GROUP_NAME --zone-name "privatelink.vaultcore.azure.net" --name $VNET_NAME --only-show-errors 2>$null
if (-not $keyvaultPrivateLinkDnsZoneLinked) {
    Write-Host "Linking privatelink DNS zone for KeyVault to VNet"
    az network private-dns link vnet create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --zone-name "privatelink.vaultcore.azure.net" `
        --name $VNET_NAME `
        --virtual-network $vnetId `
        --registration-enabled false `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Privatelink DNS zone for KeyVault is already linked to VNet"
}


# Check if the ACR already exists. If not, create it.
$acrExists = az acr show --name $ACR_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --only-show-errors 2>$null
if (-not $acrExists) {
    Write-Host "Creating Azure Container Registry: $ACR_NAME"
    az acr create `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $ACR_NAME `
        --sku Basic `
        --admin-enabled true `
        --tags ApplicationName="$APP_NAME" Environment="$ENVIRONMENT" Classification="$CLASSIFICATION" CostCenter="$COST_CENTER" Criticality="$CRITICALITY" Owner="$OWNER" Location="$LOCATION" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Azure Container Registry $ACR_NAME already exists"
}


# Check if the A record for the cluster exists in the DNS zone. If not, create it.
$clusterRecordExists = az network dns record-set a show `
    --subscription $DNS_ZONE_SUBSCRIPTION_ID `
    --resource-group $DNS_ZONE_RESOURCE_GROUP_NAME `
    --zone-name $DOMAIN_NAME `
    --name $CLUSTER_NAME `
    --only-show-errors 2>$null

if (-not $clusterRecordExists) {
    Write-Host "Creating A record for the cluster in the DNS zone"
    az network dns record-set a add-record `
        --subscription $DNS_ZONE_SUBSCRIPTION_ID `
        --resource-group $DNS_ZONE_RESOURCE_GROUP_NAME `
        --zone-name $DOMAIN_NAME `
        --record-set-name "*" `
        --ipv4-address $AKS_INGRESS_IP `
        --only-show-errors `
        --output none
}
else {
    Write-Host "A record for the cluster already exists in the DNS zone"
}


# If the VM is powered off, start it
$vmState = az vm show `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name $JUMPBOX_NAME `
    --show-details `
    --query "powerState" `
    --output tsv

if ($vmState -eq "VM deallocated") {
    Write-Host "Starting the Jumpbox VM"
    az vm start `
        --resource-group $MGMT_RESOURCE_GROUP_NAME `
        --name $JUMPBOX_NAME `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Jumpbox VM is already running"
}

# Create the setup script
$setupScript = @"
#!/bin/bash

# Check if hostname is already in /etc/hosts, if not, add it
if ! grep -q "127.0.0.1 $(hostname)" /etc/hosts; then
    echo "127.0.0.1 $(hostname)" | sudo tee -a /etc/hosts
fi

# Stop and disable systemd-resolved
sudo systemctl stop systemd-resolved
sudo systemctl disable systemd-resolved

# Remove the symbolic link if it exists
sudo unlink /etc/resolv.conf || true

# Create a new resolv.conf with Azure DNS
sudo bash -c 'echo "nameserver 168.63.129.16
nameserver 8.8.8.8
search reddog.microsoft.com" > /etc/resolv.conf'

# Make resolv.conf immutable to prevent changes
sudo chattr +i /etc/resolv.conf

# Install required packages
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common wget

# Check and install Azure CLI if not present
if ! command -v az &> /dev/null; then
    echo "Installing Azure CLI"
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
fi

# Check and install docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker"
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=`$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu `$(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt update
    sudo apt install -y docker-ce docker-ce-cli containerd.io
    sudo usermod -aG docker azureuser
    # newgrp docker # Only enable this if you want to run docker commands in the same shell
fi

# Check and install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo "Installing Certbot"
    sudo apt install -y certbot
fi

# Check and install Terraform if not present
if ! command -v terraform &> /dev/null; then
    echo "Installing Terraform"
    wget https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip
    gunzip -c terraform_1.7.5_linux_amd64.zip > terraform
    sudo mv terraform /usr/local/bin/
    sudo chmod +x /usr/local/bin/terraform
    rm terraform_1.7.5_linux_amd64.zip
fi

# Check and install kubectl if not present
if ! command -v kubectl &> /dev/null; then
    echo "Installing kubectl"
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-archive-keyring.gpg
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-archive-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list
    sudo apt-get update
    sudo apt-get install -y kubectl
fi

if ! command -v kubelogin &> /dev/null; then
    echo "Installing kubelogin"
    curl -LO "https://github.com/Azure/kubelogin/releases/latest/download/kubelogin-linux-amd64.zip"
    gunzip -c kubelogin-linux-amd64.zip > kubelogin
    sudo mv kubelogin /usr/local/bin/
    rm -rf bin kubelogin-linux-amd64.zip
    sudo chmod +x /usr/local/bin/kubelogin
fi

# Check and apply the k alias if not present
if ! grep -q "alias k='kubectl'" /home/azureuser/.bashrc; then
    echo "alias k='kubectl'" >> /home/azureuser/.bashrc
fi

# Write the .env file
cat << 'ENVEOF' > /home/azureuser/.env
ENV_FILE="$envFile"
SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
ENTRA_CLIENT_ID="$ENTRA_CLIENT_ID"
DNS_SUBSCRIPTION_ID="$dnsSubscriptionId"
DNS_RESOURCE_GROUP="$dnsResourceGroup"
MGMT_RESOURCE_GROUP_NAME="$MGMT_RESOURCE_GROUP_NAME"
MGMT_STORAGE_NAME="$MGMT_STORAGE_NAME"
ACR_NAME="$ACR_NAME"
KEYVAULT_NAME="$KEYVAULT_NAME"
JUMPBOX_NAME="$JUMPBOX_NAME"
JUMPBOX_IDENTITY_NAME="$JUMPBOX_IDENTITY_NAME"
JUMPBOX_IDENTITY_ID="$identityId"
ADMIN_GROUP_ID="$ADMIN_GROUP_ID"
LOG_ANALYTICS_READERS_GROUP_ID="$LOG_ANALYTICS_READERS_GROUP_ID"
VNET_ID="$vnetId"
WEB_SUBNET_ID="$webSubnetId"
APP_SUBNET_ID="$appSubnetId"
ENVEOF

# Create entrypoint script
cat << 'ENTRYPOINTEOF' > /home/azureuser/entrypoint.sh
#!/bin/bash

# Load environment variables
[ -f /home/azureuser/.env ] && source /home/azureuser/.env

# Clone the otto repository if it doesn't exist
[ ! -d "/home/azureuser/otto" ] && git clone https://github.com/justicecanada/otto.git /home/azureuser/otto

# Git pull the latest changes if the repo exists
[ -d "/home/azureuser/otto" ] && git -C /home/azureuser/otto pull

# Navigate to the setup directory if it exists
[ -d "/home/azureuser/otto/setup" ] && cd /home/azureuser/otto/setup

# Source the `$ENV_FILE if it exists
[ -f "`$ENV_FILE" ] && source "`$ENV_FILE"
ENTRYPOINTEOF

# Append sourcing of entrypoint script to .bashrc if not already present
if ! grep -q "source /home/azureuser/entrypoint.sh" /home/azureuser/.bashrc; then
    echo "[ -f /home/azureuser/entrypoint.sh ] && source /home/azureuser/entrypoint.sh" >> /home/azureuser/.bashrc
fi
"@

# Write the entrypoint to the Jumpbox VM
Write-Host "Writing the entrypoint to the Jumpbox VM"
az vm run-command invoke `
    --resource-group $MGMT_RESOURCE_GROUP_NAME `
    --name $JUMPBOX_NAME `
    --command-id RunShellScript `
    --scripts "$setupScript" `
    --only-show-errors `
    --output none
