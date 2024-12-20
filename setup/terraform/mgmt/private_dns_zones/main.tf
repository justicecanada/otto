resource "azurerm_private_dns_zone" "keyvault_zone" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = var.mgmt_resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "keyvault_zone_link" {
  name                  = "pdnslink-otto-${var.environment}-keyvault"
  resource_group_name   = var.mgmt_resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.keyvault_zone.name
  virtual_network_id    = var.vnet_id
  registration_enabled  = false
}

resource "azurerm_private_dns_zone" "blob_zone" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = var.mgmt_resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob_zone_link" {
  name                  = "pdnslink-otto-${var.environment}-blob"
  resource_group_name   = var.mgmt_resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.blob_zone.name
  virtual_network_id    = var.vnet_id
  registration_enabled  = false
}

resource "azurerm_private_dns_zone" "aks_dns" {
  name                = "privatelink.${var.location}.azmk8s.io"
  resource_group_name = var.mgmt_resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "aks_dns_link" {
  name                  = "pdnslink-otto-${var.environment}-aks"
  resource_group_name   = var.mgmt_resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.aks_dns.name
  virtual_network_id    = var.vnet_id
  registration_enabled  = false
}
