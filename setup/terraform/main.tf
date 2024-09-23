# CM-8 & CM-9: Defines and manages various resources, providing a documented inventory of system components

terraform {
  backend "azurerm" {}
}

# Use modules for different resources
module "resource_group" {
  source   = "./modules/resource_group"
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

# Data source for Azure AD group of KeyVault and AKS administrators
data "azuread_group" "admin_groups" {
  for_each     = toset(split(",", var.admin_group_name))
  display_name = trimspace(each.value)
}

# Data source for Azure AD group of ACR publishers
data "azuread_group" "acr_publishers" {
  for_each     = toset(split(",", var.acr_publishers_group_name))
  display_name = trimspace(each.value)
}


# Key Vault module
# SC-13: Centralized key management and cryptographic operations
module "keyvault" {
  source                 = "./modules/keyvault"
  resource_group_name    = module.resource_group.name
  location               = var.location
  keyvault_name          = var.keyvault_name
  admin_group_object_ids = values(data.azuread_group.admin_groups)[*].object_id
  entra_client_secret    = var.entra_client_secret
  tags                   = local.common_tags
}

# ACR module
module "acr" {
  source                   = "./modules/acr"
  acr_name                 = var.acr_name
  resource_group_name      = module.resource_group.name
  location                 = var.location
  acr_sku                  = "Basic"
  tags                     = local.common_tags
  acr_publisher_object_ids = values(data.azuread_group.acr_publishers)[*].object_id
  keyvault_id              = module.keyvault.keyvault_id
}

# Disk module
module "disk" {
  source               = "./modules/disk"
  disk_name            = var.disk_name
  resource_group_name  = module.resource_group.name
  location             = var.location
  tags                 = local.common_tags
  aks_cluster_id       = module.aks.aks_cluster_id
  keyvault_id          = module.keyvault.keyvault_id
  cmk_id               = module.keyvault.cmk_id
  wait_for_propagation = module.keyvault.wait_for_propagation
}

# Storage module
module "storage" {
  source                 = "./modules/storage"
  storage_name           = var.storage_name
  resource_group_name    = module.resource_group.name
  location               = var.location
  tags                   = local.common_tags
  keyvault_id            = module.keyvault.keyvault_id
  cmk_name               = module.keyvault.cmk_name
  wait_for_propagation   = module.keyvault.wait_for_propagation
  storage_container_name = var.storage_container_name
}

# DjangoDB module
module "djangodb" {
  source               = "./modules/djangodb"
  resource_name        = var.djangodb_resource_name
  resource_group_name  = module.resource_group.name
  location             = var.location
  tags                 = local.common_tags
  storage_account_id   = module.storage.storage_account_id
  keyvault_id          = module.keyvault.keyvault_id
  aks_ip_address       = module.aks.outbound_ip_address
  wait_for_propagation = module.keyvault.wait_for_propagation
}

# Cognitive Services module
module "cognitive_services" {
  source               = "./modules/cognitive_services"
  name                 = var.cognitive_services_name
  location             = var.location
  resource_group_name  = module.resource_group.name
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
  resource_group_name             = module.resource_group.name
  keyvault_id                     = module.keyvault.keyvault_id
  tags                            = local.common_tags
  wait_for_propagation            = module.keyvault.wait_for_propagation
  gpt_35_turbo_capacity           = var.gpt_35_turbo_capacity
  gpt_4_turbo_capacity            = var.gpt_4_turbo_capacity
  gpt_4o_capacity                 = var.gpt_4o_capacity
  text_embedding_3_large_capacity = var.text_embedding_3_large_capacity
}

# AKS module
module "aks" {
  source                 = "./modules/aks"
  aks_cluster_name       = var.aks_cluster_name
  location               = var.location
  resource_group_name    = module.resource_group.name
  admin_group_object_ids = values(data.azuread_group.admin_groups)[*].object_id
  keyvault_id            = module.keyvault.keyvault_id
  acr_id                 = module.acr.acr_id
  disk_encryption_set_id = module.disk.disk_encryption_set_id
  storage_account_id     = module.storage.storage_account_id
  tags                   = local.common_tags
  admin_email            = var.admin_email
}


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

# CM-8 & CM-9: Diagnostic settings for ACR
resource "azurerm_monitor_diagnostic_setting" "acr" {
  name               = "${var.acr_name}-diagnostics"
  target_resource_id = module.acr.acr_id
  storage_account_id = module.storage.storage_account_id

  enabled_log {
    category = "ContainerRegistryRepositoryEvents"
  }

  enabled_log {
    category = "ContainerRegistryLoginEvents"
  }

  metric {
    category = "AllMetrics"
  }
}
