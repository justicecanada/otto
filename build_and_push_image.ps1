$VPN_DEFAULT_GATEWAY = "10.100.64.1"

# Wait for VPN connection
while (-not (Test-Connection -ComputerName $VPN_DEFAULT_GATEWAY -Count 1 -Quiet)) {
    Write-Host "Not connected to VPN. Waiting for connection..."
    Start-Sleep -Seconds 10
}
Write-Host "VPN connection detected. Proceeding with the script..."

# Ensure Azure login and correct subscription selection
az login
Write-Host "Available subscriptions:"
az account list --query "[].{Name:name, SubscriptionId:id}" --output table
$SUBSCRIPTION_ID = Read-Host -Prompt "Enter the Subscription ID you want to use"
az account set --subscription $SUBSCRIPTION_ID

# Prompt for inputs
Write-Host "Available container registries:"
az acr list --query "[].{ResourceGroup:resourceGroup, Name:name}" --output table
$REGISTRY_NAME = Read-Host -Prompt "Enter the registry name you want to use"

$VERSION = Read-Host -Prompt "Enter the version number (e.g., v1.0.0)"

$timeout = 60  # Timeout in seconds

function Test-VPNConnection {
    try {
        $result = Test-Connection -ComputerName $VPN_DEFAULT_GATEWAY -Count 1 -Quiet -ErrorAction Stop
        return $result
    }
    catch {
        Write-Host "Unable to ping VPN gateway. Assuming disconnected."
        return $false
    }
}

if (Test-VPNConnection) {
    Write-Host "VPN connection detected. Please disconnect within $timeout seconds."

    $startTime = Get-Date
    do {
        if (-not (Test-VPNConnection)) {
            Write-Host "VPN disconnected. Proceeding with the script..."
            break
        }
        Start-Sleep -Seconds 10
        Write-Host "VPN still connected. Waiting for disconnection..."
    } while (((Get-Date) - $startTime).TotalSeconds -lt $timeout)

    if (Test-VPNConnection) {
        Write-Host "Timeout reached. VPN is still connected. Attempting to use Docker cache."
        break
    }
}

# Get the latest Git commit hash dynamically
$GITHUB_HASH = & git rev-parse HEAD

# Generate build number
$BUILD_NUMBER = Get-Date -Format "yyyyMMdd-HHmmss"

# Create version.yaml content
$versionYaml = @"
version: $VERSION
github_hash: $GITHUB_HASH
build_date: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
"@

# Write version.yaml to a temporary file
$tempFile = New-TemporaryFile
$versionYaml | Out-File -FilePath $tempFile -Encoding utf8

# Copy version.yaml to the build context directory
Copy-Item -Path $tempFile -Destination "./django/version.yaml"

# Prepare image name and tag
$IMAGE_NAME = "$($REGISTRY_NAME).azurecr.io/otto"
$SPECIFIC_TAG = "$VERSION-$BUILD_NUMBER"

# Build Docker image
docker build -t ${IMAGE_NAME}:${SPECIFIC_TAG} -f ./django/Dockerfile ./django

# Tag Docker image for ACR
docker tag ${IMAGE_NAME}:${SPECIFIC_TAG} ${IMAGE_NAME}:latest

# Wait for VPN connection
while (-not (Test-VPNConnection)) {
    Write-Host "Not connected to VPN. Waiting for connection..."
    Start-Sleep -Seconds 10
}
Write-Host "VPN connection detected. Proceeding with the script..."

# Login to ACR
az acr login --name $REGISTRY_NAME

# Push Docker image to ACR
docker push ${IMAGE_NAME}:${SPECIFIC_TAG}
docker push ${IMAGE_NAME}:latest

# Optionally, you can clean up the temporary file
Remove-Item -Path $tempFile
