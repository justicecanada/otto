
### TAGS ###

# The name that will be used for cost tracking
APP_NAME=Otto

# Cloud environment (e.g. Sandbox, Development, UAT, PreProd, Production)
ENVIRONMENT=UAT

# SA-9(5): Azure region where the resources will be deployed (e.g. canadacentral, canadaeast)
LOCATION=canadacentral

# Data classification (e.g. Unclassified, Protected B)
CLASSIFICATION=Unclassified

# Cost center for the application
COST_CENTER="Business Analytics Center (CC 12031)"

# Criticality of the application (e.g. Essential, NonEssential)
CRITICALITY=NonEssential

# Owner of the application
OWNER="Business Analytics Centre"


### VARIABLES ###

INTENDED_USE=uat
ORGANIZATION=JUS

VNET_NAME=JuDcUATPBMMOttoUATVnet
VNET_IP_RANGE=10.254.16.0/24 

APP_SUBNET_NAME=JuDcUATPBMMOttoUATAppSubnet
APP_SUBNET_IP_RANGE=10.254.16.64/28


### DYNAMIC VARIABLES ###

RESOURCE_GROUP_NAME="${APP_NAME}${INTENDED_USE^^}Rg"
KEYVAULT_NAME="${ORGANIZATION,,}-${INTENDED_USE,,}-${APP_NAME,,}-kv01"
PE_NAME="$KEYVAULT_NAME-pe"

TAGS="ApplicationName=${APP_NAME} Environment=${ENVIRONMENT} Location=${LOCATION} Classification=${CLASSIFICATION} CostCenter=\"${COST_CENTER}\" Criticality=${CRITICALITY} Owner=\"${OWNER}\""

# az keyvault purge --name $KEYVAULT_NAME --location $LOCATION --resource-group $RESOURCE_GROUP_NAME --yes
# az keyvault recover --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --location $LOCATION

# Create resource group
az group create --name $RESOURCE_GROUP_NAME --location $LOCATION

# Create virtual network and subnet
az network vnet create --resource-group $RESOURCE_GROUP_NAME --name $VNET_NAME --address-prefix $VNET_IP_RANGE --subnet-name $APP_SUBNET_NAME --subnet-prefix $APP_SUBNET_IP_RANGE

# Update subnet to disable private endpoint network policies
az network vnet subnet update --resource-group $RESOURCE_GROUP_NAME --vnet-name $VNET_NAME --name $APP_SUBNET_NAME --disable-private-endpoint-network-policies true

# Create Key Vault with public network access disabled
az keyvault create --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --location $LOCATION --network-acls-ips 0.0.0.0/0 --public-network-access Disabled

# Create private endpoint for Key Vault
az network private-endpoint create \
    --resource-group $RESOURCE_GROUP_NAME \
    --name $PE_NAME \
    --vnet-name $VNET_NAME \
    --subnet $APP_SUBNET_NAME \
    --private-connection-resource-id $(az keyvault show --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --query id -o tsv) \
    --group-id vault \
    --connection-name "KeyVaultConnection"

# Create Private DNS Zone
az network private-dns zone create --resource-group $RESOURCE_GROUP_NAME --name "privatelink.vaultcore.azure.net"

# Link Private DNS Zone to VNET
az network private-dns link vnet create \
    --resource-group $RESOURCE_GROUP_NAME \
    --zone-name "privatelink.vaultcore.azure.net" \
    --name "KeyVaultDNSLink" \
    --virtual-network $VNET_NAME \
    --registration-enabled false

# Create DNS record
NETWORK_INTERFACE_ID=$(az network private-endpoint show --name $PE_NAME --resource-group $RESOURCE_GROUP_NAME --query 'networkInterfaces[0].id' -o tsv)
PRIVATE_IP=$(az network nic show --ids $NETWORK_INTERFACE_ID --query 'ipConfigurations[0].privateIPAddress' -o tsv)
az network private-dns record-set a create --name $KEYVAULT_NAME --zone-name "privatelink.vaultcore.azure.net" --resource-group $RESOURCE_GROUP_NAME
az network private-dns record-set a add-record --record-set-name $KEYVAULT_NAME --zone-name "privatelink.vaultcore.azure.net" --resource-group $RESOURCE_GROUP_NAME --ipv4-address $PRIVATE_IP

# Create role assignment
az role assignment create \
    --role "Key Vault Secrets Officer" \
    --assignee $(az ad signed-in-user show --query id -o tsv) \
    --scope $(az keyvault show --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --query id -o tsv)




# Add network rule for ExpressRoute
az keyvault network-rule add --name $KEYVAULT_NAME --ip-address 100.94.252.216/30
az keyvault network-rule add --name $KEYVAULT_NAME --ip-address 100.94.252.220/30


# Disable public network access
az keyvault update --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --public-network-access Disabled

# Update VNET to use custom DNS servers
az network vnet update --name $VNET_NAME --resource-group $RESOURCE_GROUP_NAME --dns-servers 10.250.255.4 10.250.255.5



# Remove the 0.0.0.0/0 rule
az keyvault network-rule remove --name $KEYVAULT_NAME --ip-address "0.0.0.0/0"

# Set default action to Deny
az keyvault update --name $KEYVAULT_NAME --resource-group $RESOURCE_GROUP_NAME --default-action Deny

# Add your Cloud Shell IP temporarily if needed for testing
az keyvault network-rule add --name $KEYVAULT_NAME --ip-address "199.212.215.11"


# Add service endpoint to subnet
az network vnet subnet update \
    --resource-group $RESOURCE_GROUP_NAME \
    --vnet-name $VNET_NAME \
    --name $APP_SUBNET_NAME \
    --service-endpoints Microsoft.KeyVault

# Add virtual network rule for your subnet
az keyvault network-rule add --name $KEYVAULT_NAME --vnet-name $VNET_NAME --subnet $APP_SUBNET_NAME


# Add an example secret
az keyvault secret set --vault-name $KEYVAULT_NAME --name "ExampleSecret" --value "HelloWorld"



az keyvault network-rule add --name jus-uat-otto-kv01 --ip-address $(curl -s ifconfig.me)

# Create role assignment
az role assignment create --role "Key Vault Secrets Officer" --assignee $(az ad signed-in-user show --query id -o tsv) --scope $(az keyvault show --name jus-uat-otto-kv01 --resource-group OttoUATRg --query id -o tsv)

az keyvault secret set --vault-name jus-uat-otto-kv01 --name "ExampleSecret" --value "HelloWorld"

