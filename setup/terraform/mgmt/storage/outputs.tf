output "storage_account_id" {
  value       = azurerm_storage_account.mgmt_storage.id
  description = "The ID of the storage account"
}

output "storage_account_name" {
  value       = azurerm_storage_account.mgmt_storage.name
  description = "The name of the storage account"
}