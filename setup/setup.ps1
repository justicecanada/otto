# Create optional param for envFile
param (
    [string]$envFile = ""
)

# Function to check if a command exists
function Test-Command($cmdname) {
    return [bool](Get-Command -Name $cmdname -ErrorAction SilentlyContinue)
}
function Test-ProviderInstalled {
    param (
        [string]$ProviderName,
        [string]$Version
    )
    
    $providerPath = Join-Path $env:ProgramFiles "Terraform\plugins\registry.terraform.io\hashicorp\$ProviderName\$Version\windows_amd64\terraform-provider-${ProviderName}_v${Version}_x5.exe"
    return Test-Path $providerPath
}

# Function to show manual installation instructions
function Show-ManualInstallationInstructions($tool) {
    $response = Read-Host "$tool is not installed. Would you like instructions to install it manually? (y/N)"
    if ($response -eq 'Y' -or $response -eq 'y') {
        if ($tool -eq 'Terraform') {
            Write-Host ""
            Write-Host "To install Terraform:"
            Write-Host "1. Visit https://www.terraform.io/downloads.html"
            Write-Host "2. Download the appropriate package for your system."
            Write-Host "3. Extract the package and add the terraform binary to your PATH."
            Write-Host ""
        }
        elseif ($tool -eq 'Terragrunt') {
            Write-Host ""
            Write-Host "To install Terragrunt:"
            Write-Host "1. Visit https://github.com/gruntwork-io/terragrunt/releases"
            Write-Host "2. Download the appropriate package for your system."
            Write-Host "3. Extract the package and add the terragrunt binary to your PATH."
            Write-Host ""
        }
        Write-Host "After installation, restart this script."
        exit
    }
    else {
        Write-Host "Exiting script as $tool is required."
        exit
    }
}
function Show-ProviderInstallInstructions {
    param (
        [string]$ProviderName,
        [string]$Version
    )

    Write-Host ""
    Write-Host "The $ProviderName provider version $Version is not installed. Please follow these steps to install it:"
    Write-Host "1. Download the provider from: https://releases.hashicorp.com/terraform-provider-$ProviderName/$Version/terraform-provider-${ProviderName}_${Version}_windows_amd64.zip"
    Write-Host "2. Extract the zip file"
    Write-Host "3. Create the following directory if it doesn't exist: $env:ProgramFiles\Terraform\plugins\registry.terraform.io\hashicorp\$ProviderName\$Version\windows_amd64\"
    Write-Host "4. Copy the extracted terraform-provider-${ProviderName}_v${Version}_x5.exe file to the directory created in step 3"
    Write-Host "5. Restart this script after installing the provider"
    Write-Host ""
    exit
}
# Function to create or update terraform.rc file
function Set-TerraformRC {
    $terraformRCPath = Join-Path $env:APPDATA "terraform.rc"
    $terraformRCContent = @"
provider_installation {
  filesystem_mirror {
    path    = "C:/Program Files/Terraform/plugins"
    include = ["registry.terraform.io/*/*"]
  }
  direct {
    exclude = ["registry.terraform.io/*/*"]
  }
}
"@

    Set-Content -Path $terraformRCPath -Value $terraformRCContent -Force
    Write-Host "Terraform CLI configuration file updated at: $terraformRCPath"
}

# Check for Terraform and Terragrunt
if (-not (Test-Command terraform)) {
    Show-ManualInstallationInstructions 'Terraform'
}
if (-not (Test-Command terragrunt)) {
    Show-ManualInstallationInstructions 'Terragrunt'
}
if (-not (Test-ProviderInstalled -ProviderName "azurerm" -Version "3.78.0")) {
    Show-ProviderInstallInstructions -ProviderName "azurerm" -Version "3.78.0"
}
if (-not (Test-ProviderInstalled -ProviderName "azuread" -Version "2.45.0")) {
    Show-ProviderInstallInstructions -ProviderName "azuread" -Version "2.45.0"
}
Set-TerraformRC


if ($envFile -eq "") {
    Write-Host "Available .env files in the current directory:"
    Get-ChildItem -Filter ".env*" | ForEach-Object { $_.Name }
    $envFile = Read-Host -Prompt "Specify the .env file to use"
}

if ($envFile -eq "") {
    Write-Host "No .env file specified. Exiting..."
    exit
}

Write-Host "Using $envFile as the .env file"
Get-Content $envFile | ForEach-Object {
    if (-not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith("#")) {
        if ($_ -match '^\s*(.+?)\s*=\s*(.+?)\s*$') {
            $key = $matches[1].Trim()
            $value = $matches[2] -replace '^\s*"|"\s*$', '' -replace '^\s*''|''\s*$', ''
            Set-Variable -Name $key -Value $value.Trim()
        }
    }
}

$MGMT_RESOURCE_GROUP_NAME = "${ORGANIZATION}${ENV}${APP_NAME}MgmtRg"

# Ensure Azure login and correct subscription selection
$loggedIn = az account show --only-show-errors --output none
if ($loggedIn -eq "") {
    az login --only-show-errors --output none
}
Write-Host "Logged in as $(az account show --query 'user.name' -o tsv)"

# Set the subscription ID
$SUBSCRIPTION_ID = az account list --query "[?name=='$SUBSCRIPTION_NAME'].id" -o tsv
if ([string]::IsNullOrWhiteSpace($SUBSCRIPTION_ID)) {
    Write-Host "Subscription not found. Exiting..."
    exit
}
Write-Host "Using subscription ID: $SUBSCRIPTION_ID"
az account set --subscription $SUBSCRIPTION_ID --only-show-errors --output none

$resourceGroupExists = az group show --name $MGMT_RESOURCE_GROUP_NAME --only-show-errors 2>$null

function Get-UniqueStorageAccountName {
    param (
        [string]$Organization,
        [string]$IntendedUse,
        [string]$AppName,
        [string]$ResourceGroupName
    )

    $baseStorageName = "${Organization}${IntendedUse}${AppName}mgmt".ToLower()
    $storageName = $null

    if ($resourceGroupExists) {
        $storageName = az storage account list --resource-group $ResourceGroupName --query "[?starts_with(name, '${baseStorageName}')].name" -o tsv --only-show-errors
    }

    if (-not $storageName) {
        $random_suffix = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 5 | ForEach-Object { [char]$_ })
        $storageName = "${baseStorageName}$random_suffix".ToLower()
    }

    return $storageName
}

$MGMT_STORAGE_NAME = Get-UniqueStorageAccountName -Organization $ORGANIZATION -IntendedUse $ENV -AppName $APP_NAME -ResourceGroupName $MGMT_RESOURCE_GROUP_NAME
Write-Host "Storage account name: $MGMT_STORAGE_NAME"

$terraformDir = "terraform"
$environmentsDir = Join-Path $terraformDir "environments"
if (-not (Test-Path $environmentsDir)) {
    New-Item -ItemType Directory -Path $environmentsDir | Out-Null
}
$envSpecificDir = Join-Path $environmentsDir ".env.$ENV"
if (-not (Test-Path $envSpecificDir)) {
    New-Item -ItemType Directory -Path $envSpecificDir | Out-Null
}

# Check if jumpbox already exists
if ($resourceGroupExists) {
    $jumpboxExists = az vm list -g $MGMT_RESOURCE_GROUP_NAME --query "[?name=='$JUMPBOX_NAME']" -o tsv
    if ($jumpboxExists) {
        Write-Host "Jumpbox already exists. Please run Terraform from the jumpbox. Exiting..."
        exit
    }
}

# Create Terragrunt configuration for management setup
$terragruntConfig = @"
terraform {
  source = "../../modules//mgmt"
}

inputs = {
  resource_group_name = "$MGMT_RESOURCE_GROUP_NAME"
  location = "$LOCATION"
  vnet_name = "$VNET_NAME"
  vnet_address_space = "$VNET_IP_RANGE"
  mgmt_subnet_name = "$MGMT_SUBNET_NAME"
  mgmt_subnet_prefix = "$MGMT_SUBNET_IP_RANGE"
  storage_account_name = "$MGMT_STORAGE_NAME"
  jumpbox_name = "$JUMPBOX_NAME"
  jumpbox_identity_name = "$JUMPBOX_IDENTITY_NAME"
  bastion_vnet_name = "$BASTION_VNET_NAME"
  bastion_vnet_address_space = "$BASTION_VNET_IP_RANGE"
  bastion_subnet_prefix = "$BASTION_SUBNET_IP_RANGE"
  tags = {
    Environment = "$ENVIRONMENT"
    Classification = "$CLASSIFICATION"
    CostCenter = "$COST_CENTER"
    Criticality = "$CRITICALITY"
    Owner = "$OWNER"
  }
}
"@

# Write the Terragrunt configuration file
$terragruntHclPath = Join-Path $envSpecificDir "terragrunt.hcl"
if (-not (Test-Path $terragruntHclPath)) {
    $terragruntConfig | Out-File -FilePath $terragruntHclPath -Encoding utf8
    Write-Host "Generated terragrunt.hcl file in $terragruntHclPath"
}

Set-Location $envSpecificDir

# Initialize and apply Terragrunt
Write-Host "Initializing Terragrunt..."
terragrunt init
if ($LASTEXITCODE -ne 0) {
    Write-Host "Terragrunt initialization failed. Exiting..."
    exit 1
}

Write-Host "Applying Terragrunt configuration..."
terragrunt apply -auto-approve
if ($LASTEXITCODE -ne 0) {
    Write-Host "Terragrunt apply failed. Exiting..."
    exit 1
}

Write-Host "Initial infrastructure setup complete."

# Temporarily allow public access to the storage account
Write-Host "Temporarily allowing public access to the storage account..."
az storage account update --name $MGMT_STORAGE_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --allow-blob-public-access true --public-network-access Enabled

# Update backend configuration for remote state
$backendConfig = @"
remote_state {
  backend = "azurerm"
  config = {
    resource_group_name  = "$MGMT_RESOURCE_GROUP_NAME"
    storage_account_name = "$MGMT_STORAGE_NAME"
    container_name       = "tfstate"
    key                  = "terraform.tfstate"
  }
}
"@

# Append the backend configuration to the existing terragrunt.hcl file
if (-not (Select-String -Path $terragruntHclPath -Pattern "remote_state" -Quiet)) {
    Add-Content -Path $terragruntHclPath -Value "`n$backendConfig"
    Write-Host "Updated terragrunt.hcl with remote state configuration."
}

# Reinitialize Terragrunt with the new backend configuration
Write-Host "Reinitializing Terragrunt with remote state..."
terragrunt init -migrate-state -force-copy

# Disable public access to the storage account
Write-Host "Disabling public access to the storage account..."
az storage account update --name $MGMT_STORAGE_NAME --resource-group $MGMT_RESOURCE_GROUP_NAME --allow-blob-public-access false --public-network-access Disabled

Write-Host "State migration to Azure Storage complete. Future Terraform operations should be performed from the jumpbox."
