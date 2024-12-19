output "resource_group_id" {
  value       = module.resource_group.resource_group_id
  description = "The ID of the management resource group"
}

output "vnet_id" {
  value       = module.main_vnet.vnet_id
  description = "The ID of the virtual network"
}

output "mgmt_subnet_id" {
  value       = module.main_vnet.snet_mgmt_id
  description = "The ID of the management subnet"
}

output "app_subnet_id" {
  value       = module.main_vnet.snet_app_id
  description = "The ID of the application subnet"
}

output "web_subnet_id" {
  value       = module.main_vnet.snet_web_id
  description = "The ID of the web subnet"
}

output "storage_account_id" {
  value       = module.storage.storage_account_id
  description = "The ID of the management storage account"
}
