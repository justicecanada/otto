output "firewall_vnet_id" {
  description = "ID of the Firewall Virtual Network"
  value       = azurerm_virtual_network.firewall_vnet.id
}

output "firewall_subnet_id" {
  description = "ID of the AzureFirewallSubnet"
  value       = azurerm_subnet.fw_subnet.id
}

output "firewall_public_ip" {
  description = "Public IP address of the Azure Firewall"
  value       = azurerm_public_ip.fw_pip.ip_address
}

output "firewall_private_ip" {
  description = "Private IP address of the Azure Firewall"
  value       = azurerm_firewall.main.ip_configuration[0].private_ip_address
}

output "firewall_id" {
  description = "ID of the Azure Firewall"
  value       = azurerm_firewall.main.id
}
