output "name" {
  value       = azurerm_resource_group.rg.name
  description = "The name of the resource group"
}

output "id" {
  value       = azurerm_resource_group.rg.id
  description = "The ID of the resource group"
}

output "location" {
  value       = azurerm_resource_group.rg.location
  description = "The location of the resource group"
}
