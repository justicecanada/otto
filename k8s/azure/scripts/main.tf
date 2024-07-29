
# Use modules for different resources
module "resource_group" {
  source   = "./modules/resource_group"
  name     = local.resource_group_name
  location = var.location
  tags     = local.common_tags
}

# Data source for Azure AD group
data "azuread_group" "admin_group" {
  display_name = var.group_name
}

# Key Vault module
module "keyvault" {
  source                 = "./modules/keyvault"
  resource_group_name    = module.resource_group.name
  location               = var.location
  keyvault_name          = local.keyvault_name
  admin_keyvault_name    = local.admin_keyvault_name
  admin_group_object_id  = data.azuread_group.admin_group.object_id
  tags                   = local.common_tags
}

# ACR module
module "acr" {
  source               = "./modules/acr"
  acr_name             = local.acr_name
  resource_group_name  = module.resource_group.name
  location             = var.location
  acr_sku              = "Basic"
  tags                 = local.common_tags
  keyvault_id          = module.keyvault.keyvault_id
}

# AKS module
module "aks" {
  source                = "./modules/aks"
  aks_cluster_name      = local.aks_cluster_name
  location              = var.location
  resource_group_name   = module.resource_group.name
  admin_group_object_id = data.azuread_group.admin_group.object_id
  admin_keyvault_id     = module.keyvault.admin_keyvault_id
  keyvault_id           = module.keyvault.keyvault_id
  acr_id                = module.acr.acr_id
  tags                  = local.common_tags
}

# Disk module
module "disk" {
  source               = "./modules/disk"
  disk_name            = local.disk_name
  resource_group_name  = module.resource_group.name
  location             = var.location
  tags                 = local.common_tags
  aks_cluster_id       = module.aks.aks_cluster_id
}

# DjangoDB module
module "djangodb" {
  source                = "./modules/djangodb"
  db_name               = local.djangodb_name
  resource_group_name   = module.resource_group.name
  location              = var.location
  tags                  = local.common_tags
  keyvault_id           = module.keyvault.admin_keyvault_id
}

# Store DjangoDB hostname in Key Vault
resource "azurerm_key_vault_secret" "djangodb_hostname" {
  name         = "DJANGODB-HOSTNAME"
  value        = module.djangodb.db_hostname
  key_vault_id = module.keyvault.admin_keyvault_id
}

# Cognitive Services module
module "cognitive_services" {
  source              = "./modules/cognitive_services"
  name                = local.cognitive_services_name
  location            = var.location
  resource_group_name = module.resource_group.name
  keyvault_id         = module.keyvault.keyvault_id
  tags                = local.common_tags
}

# OpenAI module
module "openai" {
  source              = "./modules/openai"
  name                = local.openai_service_name
  location            = var.location
  resource_group_name = module.resource_group.name
  keyvault_id         = module.keyvault.keyvault_id
  tags                = local.common_tags
}

# Storage module
module "storage" {
  source              = "./modules/storage"
  storage_name        = local.storage_name
  resource_group_name = module.resource_group.name
  location            = var.location
  tags                = local.common_tags
  keyvault_id         = module.keyvault.keyvault_id
}
