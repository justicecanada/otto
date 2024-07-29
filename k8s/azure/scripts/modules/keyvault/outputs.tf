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

output "admin_keyvault_id" {
  value       = azurerm_key_vault.admin_kv.id
  description = "The ID of the admin Key Vault"
}

output "admin_keyvault_uri" {
  value       = azurerm_key_vault.admin_kv.vault_uri
  description = "The URI of the admin Key Vault"
}
