output "bastion_vnet_id" {
  value       = azurerm_virtual_network.vnet_bastion.id
  description = "The ID of the Bastion virtual network"
}

output "bastion_subnet_id" {
  value       = azurerm_subnet.snet_bastion.id
  description = "The ID of the Bastion subnet"
}

output "bastion_to_main_peering_id" {
  value       = azurerm_virtual_network_peering.vnet_peering_bastion_to_main.id
  description = "The ID of the peering from Bastion to Main VNet"
}

output "main_to_bastion_peering_id" {
  value       = azurerm_virtual_network_peering.vnet_peering_main_to_bastion.id
  description = "The ID of the peering from Main to Bastion VNet"
}