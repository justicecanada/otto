provider "azurerm" {
  features {}
}

# Generate a random password for DjangoDB
resource "random_password" "djangodb_password" {
  length  = 16
  special = true
}

resource "azurerm_cosmosdb_postgresql_cluster" "djangodb" {
  name                            = var.resource_name
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
  node_vcores                   = 4
  node_storage_quota_in_mb      = 524288
  node_server_edition           = "MemoryOptimized"
  node_public_ip_access_enabled = false # AC-22: Set to false for private access

  ha_enabled = false

  depends_on = [var.keyvault_id, random_password.djangodb_password]
}

# Store the generated password in the admin Key Vault
resource "azurerm_key_vault_secret" "djangodb_password" {
  name         = "DJANGODB-PASSWORD"
  value        = random_password.djangodb_password.result
  key_vault_id = var.keyvault_id

  depends_on = [var.keyvault_id, random_password.djangodb_password, var.wait_for_propagation]
}

# Store the generated hostname in the admin Key Vault
resource "azurerm_key_vault_secret" "djangodb_hostname" {
  name         = "DJANGODB-HOSTNAME"
  value        = azurerm_cosmosdb_postgresql_cluster.djangodb.servers[0].fqdn
  key_vault_id = var.keyvault_id

  depends_on = [var.keyvault_id, var.wait_for_propagation]
}

# # Allow public access from Azure services and resources within Azure
# resource "azurerm_cosmosdb_postgresql_firewall_rule" "allow_azure_services" {
#   name             = "AllowAzureServices"
#   cluster_id       = azurerm_cosmosdb_postgresql_cluster.djangodb.id
#   start_ip_address = "0.0.0.0"
#   end_ip_address   = "0.0.0.0"
# }

# Allow access from AKS cluster
resource "azurerm_cosmosdb_postgresql_firewall_rule" "allow_aks" {
  name             = "AllowAKS"
  cluster_id       = azurerm_cosmosdb_postgresql_cluster.djangodb.id
  start_ip_address = var.aks_ip_address
  end_ip_address   = var.aks_ip_address
}

resource "azurerm_monitor_diagnostic_setting" "djangodb_diagnostics" {
  name               = "${var.resource_name}-diagnostics"
  target_resource_id = azurerm_cosmosdb_postgresql_cluster.djangodb.id
  storage_account_id = var.storage_account_id

  enabled_log {
    category = "PostgreSQLLogs"
  }

  metric {
    category = "AllMetrics"
  }

  depends_on = [azurerm_cosmosdb_postgresql_cluster.djangodb]
}
