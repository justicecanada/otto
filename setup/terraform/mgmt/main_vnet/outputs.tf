output "vnet_id" {
  value       = azurerm_virtual_network.vnet_main.id
  description = "The ID of the main virtual network"
}

output "snet_mgmt_id" {
  value       = azurerm_subnet.snet_mgmt.id
  description = "The ID of the management subnet"
}

output "snet_app_id" {
  value       = azurerm_subnet.snet_app.id
  description = "The ID of the application subnet"
}

output "snet_web_id" {
  value       = azurerm_subnet.snet_web.id
  description = "The ID of the web subnet"
}
