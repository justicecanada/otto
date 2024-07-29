provider "azurerm" {
  features {}
}

# Generate a random password for DjangoDB
resource "random_password" "djangodb_password" {
  length  = 16
  special = true
}

resource "azurerm_cosmosdb_postgresql_cluster" "djangodb" {
  name                            = var.db_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  citus_version                   = "12.1"
  sql_version                     = "16"
  administrator_login_password    = random_password.djangodb_password.result
  node_count                      = 0
  coordinator_storage_quota_in_mb = 32768
  coordinator_vcore_count         = 1
  coordinator_server_edition      = "BurstableMemoryOptimized"
  tags                            = var.tags

  # Configure nodes
  node_vcores                     = 4
  node_storage_quota_in_mb        = 524288
  node_server_edition             = "MemoryOptimized"
  node_public_ip_access_enabled   = true

  ha_enabled                      = false
  
  depends_on = [var.keyvault_id, random_password.djangodb_password]
}

# Store the generated password in the admin Key Vault
resource "azurerm_key_vault_secret" "djangodb_password" {
  name         = "DJANGODB-PASSWORD"
  value        = random_password.djangodb_password.result
  key_vault_id = var.keyvault_id

  depends_on = [var.keyvault_id, random_password.djangodb_password]
}
