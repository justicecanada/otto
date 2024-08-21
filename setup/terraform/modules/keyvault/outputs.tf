output "keyvault_id" {
  value       = azurerm_key_vault.kv.id
  description = "The ID of the main Key Vault"
}

output "keyvault_name" {
  value       = azurerm_key_vault.kv.name
  description = "The name of the main Key Vault"
}

output "keyvault_uri" {
  value       = azurerm_key_vault.kv.vault_uri
  description = "The URI of the main Key Vault"
}

output "cmk_name" {
  value       = azurerm_key_vault_key.cmk.name
  description = "The name of the Customer Managed Key"
}

output "cmk_id" {
  value       = azurerm_key_vault_key.cmk.id
  description = "The ID of the Customer Managed Key"
}

output "wait_for_propagation" {
  value       = null_resource.wait_for_permission_propagation.id
  description = "The flag indicating that propagation has completed"
}
