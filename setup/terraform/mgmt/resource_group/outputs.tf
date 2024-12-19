output "resource_group_id" {
  value       = azurerm_resource_group.mgmt_rg.id
  description = "The ID of the management resource group"
}

output "name" {
  value       = azurerm_resource_group.mgmt_rg.name
  description = "The name of the management resource group"
  
}