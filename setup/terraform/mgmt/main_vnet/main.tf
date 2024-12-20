resource "azurerm_virtual_network" "vnet_main" {
  name                = var.vnet_name
  address_space       = [var.vnet_address_space]
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_servers         = ["168.63.129.16", "10.250.255.4", "10.250.255.5"]
  tags                = var.tags
}

resource "azurerm_subnet" "snet_mgmt" {
  name                 = var.mgmt_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet_main.name
  address_prefixes     = [var.mgmt_subnet_prefix]
  service_endpoints    = ["Microsoft.KeyVault", "Microsoft.Storage", "Microsoft.ContainerRegistry"]
}

resource "azurerm_subnet" "snet_app" {
  name                 = var.app_subnet_name
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet_main.name
  address_prefixes     = [var.app_subnet_prefix]
  service_endpoints    = ["Microsoft.KeyVault", "Microsoft.Storage", "Microsoft.ContainerRegistry"]
}

resource "azurerm_network_security_group" "nsg_mgmt" {
  name                = "nsg-mgmt-otto-${var.environment}"
  location            = var.location
  resource_group_name = var.resource_group_name

  security_rule {
    name                       = "AllowAllInbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = var.tags
}

resource "azurerm_subnet_network_security_group_association" "nsg_mgmt_association" {
  subnet_id                 = azurerm_subnet.snet_mgmt.id
  network_security_group_id = azurerm_network_security_group.nsg_mgmt.id
}

resource "azurerm_network_security_group" "nsg_app" {
  name                = "nsg-app-otto-${var.environment}"
  location            = var.location
  resource_group_name = var.resource_group_name

  security_rule {
    name                       = "AllowAllInbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = var.tags
}

resource "azurerm_subnet_network_security_group_association" "nsg_app_association" {
  subnet_id                 = azurerm_subnet.snet_app.id
  network_security_group_id = azurerm_network_security_group.nsg_app.id
}
