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
  location                        = var.location # SA-9(5): Store data in a location that complies with data residency requirements
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
  node_public_ip_access_enabled = !var.use_private_network # AC-22, IA-8: Set to false for private access

  ha_enabled = false

  depends_on = [var.keyvault_id, random_password.djangodb_password]
}

# Create a private endpoint for the Cosmos DB for PostgreSQL cluster
resource "azurerm_private_endpoint" "djangodb" {
  count               = var.use_private_network ? 1 : 0
  name                = "${var.resource_name}-endpoint"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.db_subnet_id

  private_service_connection {
    name                           = "${var.resource_name}-privateserviceconnection"
    private_connection_resource_id = azurerm_cosmosdb_postgresql_cluster.djangodb.id
    is_manual_connection           = false
    subresource_names              = ["coordinator"]
  }
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
# Only create this rule if not using private networking
resource "azurerm_cosmosdb_postgresql_firewall_rule" "allow_aks" {
  count            = var.use_private_network ? 0 : 1
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



locals { admin_email_list = split(",", var.admin_email) }

# SC-5(3): Create an action group for AKS alerts
resource "azurerm_monitor_action_group" "cosmos_alerts" {
  name                = "${var.resource_name}-alert-group"
  resource_group_name = var.resource_group_name
  short_name          = "cosmosalerts"

  dynamic "email_receiver" {
    for_each = local.admin_email_list
    content {
      name                    = "admin${index(local.admin_email_list, email_receiver.value) + 1}"
      email_address           = trimspace(email_receiver.value)
      use_common_alert_schema = true
    }
  }
}

resource "azurerm_monitor_metric_alert" "cosmos_db_cpu_alert" {
  name                = "${var.resource_name}-high-cpu-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_cosmosdb_postgresql_cluster.djangodb.id]

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/serverGroupsv2"
    metric_name      = "cpu_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
  }

  action {
    action_group_id = azurerm_monitor_action_group.cosmos_alerts.id
  }
}

resource "azurerm_monitor_metric_alert" "cosmos_db_memory_alert" {
  name                = "${var.resource_name}-high-memory-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_cosmosdb_postgresql_cluster.djangodb.id]

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/serverGroupsv2"
    metric_name      = "memory_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
  }

  action {
    action_group_id = azurerm_monitor_action_group.cosmos_alerts.id
  }
}

resource "azurerm_monitor_metric_alert" "cosmos_db_disk_alert" {
  name                = "${var.resource_name}-high-disk-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_cosmosdb_postgresql_cluster.djangodb.id]

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/serverGroupsv2"
    metric_name      = "storage_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
  }

  action {
    action_group_id = azurerm_monitor_action_group.cosmos_alerts.id
  }
}
