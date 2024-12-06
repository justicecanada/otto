# CM-8 & CM-9: Defines and manages various resources, providing a documented inventory of system components

terraform {
  # Determines where and how the Terraform state is stored
  # Is used when Terraform needs to read or write state data
  # Relies on the ARM environment variables
  backend "azurerm" {}
}

# Resource group for app resources
resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

# User-assigned managed identity for app resources
resource "azurerm_user_assigned_identity" "otto_identity" {
  name                = "otto-identity"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.common_tags
}

# Assign Network Contributor role to otto-identity
resource "azurerm_role_assignment" "otto_identity_network_contributor" {
  principal_id         = azurerm_user_assigned_identity.otto_identity.principal_id
  role_definition_name = "Network Contributor"
  scope                = var.vnet_id
}

# Key Vault module
# SC-13: Centralized key management and cryptographic operations
module "keyvault" {
  source                 = "./modules/keyvault"
  resource_group_name    = var.resource_group_name
  mgmt_resource_group_name = var.mgmt_resource_group_name
  location               = var.location
  keyvault_name          = var.keyvault_name
  jumpbox_identity_id    = var.jumpbox_identity_id
  tags                   = local.common_tags
  use_private_network    = var.use_private_network
  vnet_id                = var.vnet_id
  app_subnet_id          = var.app_subnet_id
  web_subnet_id          = var.web_subnet_id
}

# Disk module
module "disk" {
  source                    = "./modules/disk"
  disk_name                 = var.disk_name
  resource_group_name       = var.resource_group_name
  location                  = var.location
  identity_id               = azurerm_user_assigned_identity.otto_identity.id
  tags                      = local.common_tags
  aks_cluster_id            = module.aks.aks_cluster_id
  keyvault_id               = module.keyvault.keyvault_id
  cmk_id                    = module.keyvault.cmk_id
  wait_for_propagation      = module.keyvault.wait_for_propagation
  use_private_network       = var.use_private_network
  app_subnet_id             = var.app_subnet_id
  web_subnet_id             = var.web_subnet_id
}

# Storage module
module "storage" {
  source                 = "./modules/storage"
  storage_name           = var.storage_name
  resource_group_name    = var.resource_group_name
  mgmt_resource_group_name = var.mgmt_resource_group_name
  location               = var.location
  tags                   = local.common_tags
  jumpbox_identity_id    = var.jumpbox_identity_id
  keyvault_id            = module.keyvault.keyvault_id
  cmk_name               = module.keyvault.cmk_name
  wait_for_propagation   = module.keyvault.wait_for_propagation
  storage_container_name = var.storage_container_name
  use_private_network    = var.use_private_network
  vnet_id                = var.vnet_id
  app_subnet_id          = var.app_subnet_id
  web_subnet_id          = var.web_subnet_id
}

# Cognitive Services module
module "cognitive_services" {
  source               = "./modules/cognitive_services"
  name                 = var.cognitive_services_name
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_id   = module.storage.storage_account_id
  keyvault_id          = module.keyvault.keyvault_id
  wait_for_propagation = module.keyvault.wait_for_propagation
  tags                 = local.common_tags
}

# OpenAI module
module "openai" {
  source                          = "./modules/openai"
  name                            = var.openai_service_name
  location                        = var.location
  resource_group_name             = var.resource_group_name
  keyvault_id                     = module.keyvault.keyvault_id
  tags                            = local.common_tags
  wait_for_propagation            = module.keyvault.wait_for_propagation
  gpt_35_turbo_capacity           = var.gpt_35_turbo_capacity
  gpt_4_turbo_capacity            = var.gpt_4_turbo_capacity
  gpt_4o_capacity                 = var.gpt_4o_capacity
  gpt_4o_mini_capacity            = var.gpt_4o_mini_capacity
  text_embedding_3_large_capacity = var.text_embedding_3_large_capacity
}

# AKS module
module "aks" {
  source                                 = "./modules/aks"
  aks_cluster_name                       = var.aks_cluster_name
  location                               = var.location
  resource_group_name                    = var.resource_group_name
  log_analytics_readers_group_id         = split(",", var.log_analytics_readers_group_id)
  keyvault_id                            = module.keyvault.keyvault_id
  acr_id                                 = var.acr_id
  disk_encryption_set_id                 = module.disk.disk_encryption_set_id
  storage_account_id                     = module.storage.storage_account_id
  tags                                   = local.common_tags
  admin_email                            = var.admin_email
  use_private_network                    = var.use_private_network
  vm_size                                = var.vm_size
  vm_cpu_count                           = var.vm_cpu_count
  approved_cpu_quota                     = var.approved_cpu_quota
  vnet_id                                = var.vnet_id
  web_subnet_id                          = var.web_subnet_id
  jumpbox_identity_id                    = var.jumpbox_identity_id
}

# TODO: Uncomment Velero after the change request is approved
# # Velero module
# module "velero" {
#   source               = "./modules/velero"
#   resource_group_name  = var.resource_group_name
#   velero_identity_name = var.velero_identity_name
#   location             = var.location
#   oidc_issuer_url      = module.aks.oidc_issuer_url
#   storage_account_id   = module.storage.storage_account_id
# }

# CM-8 & CM-9: Diagnostic settings for Key Vault
resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name               = "${var.keyvault_name}-diagnostics"
  target_resource_id = module.keyvault.keyvault_id
  storage_account_id = module.storage.storage_account_id

  enabled_log {
    category = "AuditEvent"
  }

  metric {
    category = "AllMetrics"
  }
}
