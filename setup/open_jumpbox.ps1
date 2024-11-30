param (
    [string]$subscription = "",
    [string]$mgmtGroup = "",
    [string]$jumpbox = "",
    [int]$connectChoice = 1 # 1 for Azure CLI, 2 for SSH tunnel
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


# Select the Jumpbox VM
$jumpboxIds = az vm list --subscription $SUBSCRIPTION_ID --query "[?name=='jumpbox'].id" -o tsv
$count = ($jumpboxIds -split '\n').Count
if ($count -ne 1) {
    Write-Host "Error: Found $count jumpbox VMs. Expected exactly one."
    exit 1
}
$vmId = $jumpboxIds
$mgmtGroup = az vm show --ids $vmId --query "resourceGroup" -o tsv
Write-Host "Using the jumpbox VM in resource group $mgmtGroup"


# TODO: Consider using RBAC and JIT VM once Defender for Cloud is available

# If the current user doesn't have an SSH key, generate one
if (-not (Test-Path "~/.ssh/id_rsa")) {
    Write-Host "Generating SSH key"
    ssh-keygen -t rsa -b 4096 -N '""' -f ~/.ssh/id_rsa
}

# Update the Jumpbox VM with the SSH key
Write-Host "Updating the Jumpbox VM with the SSH key"
$sshPublicKey = Get-Content "~/.ssh/id_rsa.pub" -Raw
az vm user update `
    --ids $vmId `
    --username azureuser `
    --ssh-key-value $sshPublicKey `
    --only-show-errors `
    --output none


if ($connectChoice -eq 1) {
    # Connect using Azure CLI
    Write-Host "Connecting to the Jumpbox VM"
    az network bastion ssh `
        --resource-group $mgmtGroup `
        --name bastion `
        --target-resource-id $vmId `
        --auth-type ssh-key `
        --username azureuser `
        --ssh-key "$HOME/.ssh/id_rsa" `
        --only-show-errors `
        --output none
}
elseif ($connectChoice -eq 2) {
    # Create an SSH tunnel to the Jumpbox VM
    Write-Host "Creating an SSH tunnel to the Jumpbox VM"
    az network bastion create-ssh-tunnel `
        --resource-group $mgmtGroup `
        --name bastion `
        --target-resource-id $vmId `
        --auth-type ssh-key `
        --username azureuser `
        --ssh-key "$HOME/.ssh/id_rsa" `
        --only-show-errors `
        --output none
}
else {
    Write-Host "Exiting without connecting to the Jumpbox VM"
}

# Prompt the user if they want to turn off the VM
$turnOff = Read-Host -Prompt "Do you want to turn off the Jumpbox VM to save costs? (y/n)"

# Prompt the user if they want to delete the Bastion service,
$deleteBastion = Read-Host -Prompt "Do you want to deallocate the Bastion service to save costs? (y/n)"

if ($turnOff -eq "y") {
    Write-Host "Turning off the Jumpbox VM"
    az vm deallocate --ids $vmId --only-show-errors --output none
}
else {
    Write-Host "Jumpbox VM will continue to incur costs"
}

if ($deleteBastion -eq "y") {
    Write-Host "Deallocating the Bastion service"
    az network bastion delete --resource-group $mgmtGroup --name bastion --only-show-errors --output none
}
else {
    Write-Host "Bastion service will continue to incur costs"
}
