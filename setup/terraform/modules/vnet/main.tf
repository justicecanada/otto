resource "azurerm_virtual_network" "vnet" {
  name                = var.vnet_name
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_ip_range]
  # Corporate DNS servers and Google DNS servers
  dns_servers = ["10.250.255.4", "10.250.255.5", "8.8.8.8", "8.8.4.4"]

  tags = var.tags
}

# AKS Cluster
resource "azurerm_subnet" "web_subnet" {
  name                 = var.web_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.web_subnet_ip_range]

  # Enable service endpoints for Key Vault, Storage, and Container Registry so that the AKS cluster can access these services
  # This is typically only required when the AKS is using Azure CNI networking rather than Kubenet
  # service_endpoints = ["Microsoft.KeyVault", "Microsoft.Storage", "Microsoft.ContainerRegistry"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

# Key Vault & Storage
resource "azurerm_subnet" "app_subnet" {
  name                 = var.app_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.app_subnet_ip_range]
  service_endpoints    = ["Microsoft.KeyVault", "Microsoft.Storage"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

# Cosmos DB for PostgreSQL
resource "azurerm_subnet" "db_subnet" {
  name                 = var.db_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.db_subnet_ip_range]
  service_endpoints    = ["Microsoft.KeyVault", "Microsoft.Storage"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}


# Create a Network Security Group (NSG) to control access to the disks
resource "azurerm_network_security_group" "nsg" {
  name                = "${var.vnet_name}-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name

  security_rule {
    name                       = "AllowCorporateIP"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "${var.corporate_ip}/32"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowWebSubnet"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowAppSubnet"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = azurerm_subnet.app_subnet.address_prefixes[0]
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowDbSubnet"
    priority                   = 130
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = azurerm_subnet.db_subnet.address_prefixes[0]
    destination_address_prefix = "*"
  }

  # The pod_cidr (10.244.0.0/16) is separate from the subnet ranges.
  # In kubenet, pods get IPs from this range, which is managed by Kubernetes,
  # not Azure. This allows for efficient IP usage without exhausting the VNet IP range.    
  security_rule {
    name                       = "AllowAKSPods"
    priority                   = 140
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "10.244.0.0/16"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  # Allow outbound traffic to the internet from the AKS cluster so that pods can pull container images
  security_rule {
    name                       = "AllowInternetOutbound"
    priority                   = 4000
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  tags = var.tags
}

# Associate the NSG with the subnets where the private endpoints are located
resource "azurerm_subnet_network_security_group_association" "web_subnet_nsg_association" {
  subnet_id                 = azurerm_subnet.web_subnet.id
  network_security_group_id = azurerm_network_security_group.nsg.id
}

resource "azurerm_subnet_network_security_group_association" "app_subnet_nsg_association" {
  subnet_id                 = azurerm_subnet.app_subnet.id
  network_security_group_id = azurerm_network_security_group.nsg.id
}

resource "azurerm_subnet_network_security_group_association" "db_subnet_nsg_association" {
  subnet_id                 = azurerm_subnet.db_subnet.id
  network_security_group_id = azurerm_network_security_group.nsg.id
}

# This route table is required for kubenet. AKS will automatically manage
# the necessary routes for pod communication.
resource "azurerm_route_table" "aks_route_table" {
  name                = "${var.vnet_name}-routetable"
  location            = var.location
  resource_group_name = var.resource_group_name

  # This default route sends all traffic to the internet
  # It's necessary for outbound connectivity from the cluster
  route {
    name           = "default"
    address_prefix = "0.0.0.0/0"
    next_hop_type  = "Internet"
  }

  tags = var.tags
}

# This associates the route table with the AKS subnet only
resource "azurerm_subnet_route_table_association" "web_subnet_route_table" {
  subnet_id      = azurerm_subnet.web_subnet.id
  route_table_id = azurerm_route_table.aks_route_table.id
}
