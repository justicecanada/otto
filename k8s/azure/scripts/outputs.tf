output "name" {
  value       = module.resource_group.name
  description = "The name of the resource group"
}

output "storage_account_name" {
  value       = module.storage.storage_account_name
  description = "The name of the created storage account"
}

output "storage_primary_blob_endpoint" {
  value       = module.storage.primary_blob_endpoint
  description = "The primary blob endpoint URL"
}

output "resource_group_name" {
  value       = local.resource_group_name
}

output "aks_cluster_name" {
  value       = local.aks_cluster_name
}
