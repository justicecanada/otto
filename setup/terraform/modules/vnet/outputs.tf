output "vnet_id" {
  value       = azurerm_virtual_network.vnet.id
  description = "The ID of the virtual network"
}

output "web_subnet_id" {
  value       = azurerm_subnet.web_subnet.id
  description = "The ID of the web subnet"
}

output "app_subnet_id" {
  value       = azurerm_subnet.app_subnet.id
  description = "The ID of the app subnet"
}

output "db_subnet_id" {
  value       = azurerm_subnet.db_subnet.id
  description = "The ID of the database subnet"
}

output "web_subnet_address_prefix" {
  value       = azurerm_subnet.web_subnet.address_prefixes[0]
  description = "The address prefix of the web subnet"
}

output "app_subnet_address_prefix" {
  value       = azurerm_subnet.app_subnet.address_prefixes[0]
  description = "The address prefix of the app subnet"
}

output "db_subnet_address_prefix" {
  value       = azurerm_subnet.db_subnet.address_prefixes[0]
  description = "The address prefix of the database subnet"
}
