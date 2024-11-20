resource "azurerm_virtual_network" "vnet" {
  name                = var.vnet_name
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.vnet_ip_range]
  dns_servers         = ["10.250.255.4", "10.250.255.5"]

  tags = var.tags
}

# AKS Cluster
resource "azurerm_subnet" "web_subnet" {
  name                 = var.web_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.web_subnet_ip_range]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

# Key Vault & Storage
resource "azurerm_subnet" "app_subnet" {
  name                 = var.app_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.app_subnet_ip_range]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

# Cosmos DB for PostgreSQL
resource "azurerm_subnet" "db_subnet" {
  name                 = var.db_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = [var.db_subnet_ip_range]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}
