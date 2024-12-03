resource "azurerm_user_assigned_identity" "storage_identity" {
  resource_group_name = var.resource_group_name
  location            = var.location
  name                = "${var.storage_name}-identity"
  tags                = var.tags
}

# Assign "Key Vault Crypto Service Encryption User" role
resource "azurerm_role_assignment" "storage_key_vault_crypto_user" {
  scope                = var.keyvault_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.storage_identity.principal_id
}

# Assign "Key Vault Secrets User" role
resource "azurerm_role_assignment" "storage_key_vault_secrets_user" {
  scope                = var.keyvault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.storage_identity.principal_id
}

# Add a delay to allow for the permissions to propagate
resource "null_resource" "wait_for_storage_permission_propagation" {
  provisioner "local-exec" {
    command = "sleep 120"
  }
  depends_on = [
    azurerm_role_assignment.storage_key_vault_crypto_user,
    azurerm_role_assignment.storage_key_vault_secrets_user
  ]
}

resource "azurerm_key_vault_key" "storage_cmk" {
  # SC-12 & SC-13: Customer-managed keys for storage encryption
  key_vault_id = var.keyvault_id
  name         = var.cmk_name
  key_type     = "RSA"
  key_size     = 2048

  key_opts = [
    "decrypt",
    "encrypt",
    "sign",
    "unwrapKey",
    "verify",
    "wrapKey",
  ]
}

# SC-28: Storage account encryption by default using 256-bit AES encryption
# SC-8: Azure Storage implicitly enables secure transfer
resource "azurerm_storage_account" "storage" {
  name                     = var.storage_name
  resource_group_name      = var.resource_group_name
  location                 = var.location # SA-9(5): Store data in a location that complies with data residency requirements
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  # TODO: Uncomment when SSC routes all traffic to the VNET through ExpressRoute
  # public_network_access_enabled = !var.use_private_network # AC-22, IA-8: Set to false for private access

  default_to_oauth_authentication = true
  is_hns_enabled                  = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.storage_identity.id]
  }

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  customer_managed_key {
    key_vault_key_id          = azurerm_key_vault_key.storage_cmk.id
    user_assigned_identity_id = azurerm_user_assigned_identity.storage_identity.id
  }

  # TODO: Uncomment when SSC routes all traffic to the VNET through ExpressRoute
  # network_rules {
  #   default_action = var.use_private_network ? "Deny" : "Allow"
  #   bypass         = ["AzureServices"]
  # }

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
    ip_rules                   = [var.corporate_public_ip] # Allow access from the corporate network for management purposes
    virtual_network_subnet_ids = [var.app_subnet_id]       # Allow access from the app subnets
  }

  tags = var.tags

  depends_on = [azurerm_key_vault_key.storage_cmk, null_resource.wait_for_storage_permission_propagation, var.app_subnet_id, var.web_subnet_id, var.db_subnet_id]
}

# TODO: Uncomment when SSC routes all traffic to the VNET through ExpressRoute
# resource "azurerm_private_endpoint" "storage_blob" {
#   count               = var.use_private_network ? 1 : 0
#   name                = "${var.storage_name}-blob-endpoint"
#   location            = var.location
#   resource_group_name = var.resource_group_name
#   subnet_id           = var.app_subnet_id

#   private_service_connection {
#     name                           = "${var.storage_name}-blob-connection"
#     private_connection_resource_id = azurerm_storage_account.storage.id
#     is_manual_connection           = false
#     subresource_names              = ["blob"]
#   }
# }

resource "azurerm_storage_management_policy" "lifecycle" {
  storage_account_id = azurerm_storage_account.storage.id

  rule {
    name    = "delete-old-logs"
    enabled = true
    filters {
      prefix_match = ["insights-logs-", "insights-metrics-"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 30
      }
    }
  }

  rule {
    name    = "retention-policy"
    enabled = true
    filters {
      blob_types = ["blockBlob"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = 30
        tier_to_archive_after_days_since_modification_greater_than = 90
        delete_after_days_since_modification_greater_than          = 365
      }
    }
  }
}

resource "azurerm_storage_container" "storage_container" {
  name                  = var.storage_container_name
  storage_account_name  = azurerm_storage_account.storage.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "backups_container" {
  name                  = var.backup_container_name
  storage_account_name  = azurerm_storage_account.storage.name
  container_access_type = "private"

  depends_on = [azurerm_storage_account.storage]
}

# Add a delay to allow for the storage to be created 
resource "null_resource" "wait_for_storage_account" {
  provisioner "local-exec" {
    command = "sleep 120"
  }
  depends_on = [var.keyvault_id, azurerm_storage_account.storage, var.wait_for_propagation]
}

# Assign "Storage Blob Data Owner" role to the user-assigned identity
resource "azurerm_role_assignment" "storage_identity_data_owner" {
  scope                = azurerm_storage_account.storage.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.storage_identity.principal_id
}

# Assign "Storage Blob Data Owner" role to the admin group
resource "azurerm_role_assignment" "storage_admin" {
  for_each             = toset(var.admin_group_object_ids)
  scope                = azurerm_storage_account.storage.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = each.value
}
