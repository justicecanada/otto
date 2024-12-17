#!/bin/bash

VNET_NAME="JuDcDevPBMMOttoDevVnet"
VNET_IP_RANGE="10.254.12.0/25"

WEB_SUBNET_NAME="JuDcDevPBMMOttoDevWebSubnet"
WEB_SUBNET_IP_RANGE="10.254.12.0/26"

MGMT_SUBNET_NAME="JuDcDevPBMMOttoDevMgmtSubnet"
MGMT_SUBNET_IP_RANGE="10.254.12.80/28"

SUBSCRIPTION="OttoDev"
LOCATION="canadacentral"
APP_NAME="Otto"
ENVIRONMENT="Development"
CLASSIFICATION="Unclassified"
COST_CENTER="Business Analytics Center (CC 12031)"
CRITICALITY="NonEssential"
OWNER="Business Analytics Centre"

resourceGroupName='HelloWorldRg'
appServiceName='otto-hello-world'

vnetResourceGroupName='OttoDEVMgmtRg'
existingRouteTableName='jus-dev-otto-aks-rt'
firewallIPAddress='10.250.6.1'

az login

SUBSCRIPTION_ID=$(az account list \
    --query "[?name=='$SUBSCRIPTION'].id" -o tsv)

az account set \
    --subscription "$SUBSCRIPTION_ID"

# Create resource group and App Service
az group create \
    --name "$resourceGroupName" \
    --location "$LOCATION" \
    --tags ApplicationName="$APP_NAME" \
    Environment="$ENVIRONMENT" \
    Classification="$CLASSIFICATION" \
    CostCenter="$COST_CENTER" \
    Criticality="$CRITICALITY" \
    Owner="$OWNER" \
    Location="$LOCATION"

# Create the web app
az webapp up \
    --name "$appServiceName" \
    --resource-group "$resourceGroupName" \
    --location "$LOCATION" \
    --runtime "PYTHON:3.9" \
    --sku B1 \
    --logs

# Get the Web App ID
WEBAPP_ID=$(az webapp show --name "$appServiceName" --resource-group "$resourceGroupName" --query id -o tsv)

subnetId=$(az network vnet subnet show \
    --name "$WEB_SUBNET_NAME" \
    --vnet-name "$VNET_NAME" \
    --resource-group "$vnetResourceGroupName" \
    --query id -o tsv)

# Add a new route for the HelloWorld app to the existing route table
# NOTE: This step will fail if the AKS route is already in the route table
az network route-table route create \
    --name HelloWorldRoute \
    --resource-group "$vnetResourceGroupName" \
    --route-table-name "$existingRouteTableName" \
    --address-prefix "$WEB_SUBNET_IP_RANGE" \
    --next-hop-type VirtualAppliance \
    --next-hop-ip-address "$firewallIPAddress"

# Associate the route table with the subnet if not already associated
az network vnet subnet update \
    --name "$WEB_SUBNET_NAME" \
    --vnet-name "$VNET_NAME" \
    --resource-group "$vnetResourceGroupName" \
    --route-table "$existingRouteTableName"

# Create the private endpoint
az network private-endpoint create \
    --name HelloWorldPrivateEndpoint \
    --resource-group "$resourceGroupName" \
    --subnet "$subnetId" \
    --private-connection-resource-id "$WEBAPP_ID" \
    --group-id sites \
    --connection-name HelloWorldConnection

# Create the private DNS zone
az network private-dns zone create \
    --resource-group "$resourceGroupName" \
    --name "privatelink.azurewebsites.net"

# Create the DNS link to the VNet
az network private-dns link vnet create \
    --resource-group "$resourceGroupName" \
    --zone-name "privatelink.azurewebsites.net" \
    --name HelloWorldDNSLink \
    --virtual-network "$(az network vnet show --name "$VNET_NAME" --resource-group "$vnetResourceGroupName" --query id -o tsv)" \
    --registration-enabled false

# Create the DNS zone group
az network private-endpoint dns-zone-group create \
    --resource-group "$resourceGroupName" \
    --endpoint-name HelloWorldPrivateEndpoint \
    --name HelloWorldZoneGroup \
    --private-dns-zone "privatelink.azurewebsites.net" \
    --zone-name webapp

# From the VM, test the connection to the private endpoint
curl -L otto-hello-world.azurewebsites.net
# Expected output:
# 
# <!DOCTYPE html>
# <html lang="en">
# <head>
#     <meta charset="utf-8" />
#     <meta name="viewport" content="width=device-width, initial-scale=1.0" />
#     <meta http-equiv="X-UA-Compatible" content="IE=edge" />
#     <title>Microsoft Azure App Service - Welcome</title>
# ...
