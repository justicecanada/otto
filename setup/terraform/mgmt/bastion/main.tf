resource "azurerm_virtual_network" "vnet_bastion" {
  name                = "vnet-${var.bastion_name}"
  address_space       = [var.bastion_vnet_address_space]
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_servers         = ["168.63.129.16"]
}

resource "azurerm_subnet" "snet_bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.vnet_bastion.name
  address_prefixes     = [var.bastion_subnet_prefix]
}

resource "azurerm_public_ip" "pip_bastion" {
  name                = "pip-${var.bastion_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1"]
}

resource "azurerm_bastion_host" "bas" {
  name                = "bas-${var.bastion_name}"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                 = "bastion-ipconfig"
    subnet_id            = azurerm_subnet.snet_bastion.id
    public_ip_address_id = azurerm_public_ip.pip_bastion.id
  }

  sku                    = "Standard"
  copy_paste_enabled     = false
  file_copy_enabled      = false
  ip_connect_enabled     = true
  shareable_link_enabled = false
  tunneling_enabled      = false
}

resource "azurerm_virtual_network_peering" "vnet_peering_bastion_to_main" {
  name                         = "peer-bastion-to-main"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = azurerm_virtual_network.vnet_bastion.id
  remote_virtual_network_id    = var.main_vnet_id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "vnet_peering_main_to_bastion" {
  name                         = "peer-main-to-bastion"
  resource_group_name          = var.resource_group_name
  virtual_network_name         = var.main_vnet_id
  remote_virtual_network_id    = azurerm_virtual_network.vnet_bastion.id
  allow_virtual_network_access = true
}