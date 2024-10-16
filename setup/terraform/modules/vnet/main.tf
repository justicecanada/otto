resource "azurerm_virtual_network" "vnet" {
  name                = var.vnet_name
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = ["10.255.0.0/23"]

  tags = var.tags
}

resource "azurerm_subnet" "web_subnet" {
  name                 = "${var.vnet_name}-web"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.255.0.0/25"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_subnet" "app_subnet" {
  name                 = "${var.vnet_name}-app"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.255.0.128/25"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_subnet" "db_subnet" {
  name                 = "${var.vnet_name}-db"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.255.1.0/25"]

  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}
