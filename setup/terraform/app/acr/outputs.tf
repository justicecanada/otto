output "acr_name" {
  value       = azurerm_container_registry.acr.name
  description = "The name of the ACR"
}

output "acr_id" {
  value       = azurerm_container_registry.acr.id
  description = "The ID of the ACR"
}
