# Add a delay to allow for the key vault's purge protection to be enabled 
resource "null_resource" "wait_for_purge_protection" {
  provisioner "local-exec" {
    command = "sleep 120"
  }
  depends_on = [var.wait_for_propagation, var.cmk_id]
}

resource "azurerm_disk_encryption_set" "des" {
  name                = "${var.disk_name}-des"
  location            = var.location # SA-9(5): Store data in a location that complies with data residency requirements
  resource_group_name = var.resource_group_name
  key_vault_key_id    = var.cmk_id

  identity {
    type = "SystemAssigned"
  }

  depends_on = [null_resource.wait_for_purge_protection]
}

# Add a delay to allow for the disk encryption set to be created 
resource "null_resource" "wait_for_disk_encryption_set" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [var.wait_for_propagation, azurerm_disk_encryption_set.des]
}

resource "azurerm_role_assignment" "des_key_vault_crypto_user" {
  scope                = var.keyvault_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_disk_encryption_set.des.identity[0].principal_id

  depends_on = [azurerm_disk_encryption_set.des, null_resource.wait_for_disk_encryption_set]
}

# Add a delay to allow for the disk encryption set permissions to be propagated
resource "null_resource" "wait_for_disk_encryption_set_permissions" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azurerm_role_assignment.des_key_vault_crypto_user, null_resource.wait_for_disk_encryption_set]
}

# SSD for static files and performance-sensitive data
resource "azurerm_managed_disk" "aks_ssd_disk" {
  name                 = "${var.disk_name}-ssd"
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.ssd_disk_size
  os_type              = "Linux"

  public_network_access_enabled = !var.use_private_network
  disk_encryption_set_id        = azurerm_disk_encryption_set.des.id # SC-28 & SC-28(1): Customer-managed keys for enhanced encryption control

  tags = merge(var.tags, {
    "Purpose" = "Static files and performance-sensitive data"
  })

  depends_on = [var.aks_cluster_id, null_resource.wait_for_disk_encryption_set_permissions]
}

# HDD for larger, less frequently accessed data
resource "azurerm_managed_disk" "aks_hdd_disk" {
  name                 = "${var.disk_name}-hdd"
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_type = "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.hdd_disk_size
  os_type              = "Linux"

  public_network_access_enabled = !var.use_private_network
  disk_encryption_set_id        = azurerm_disk_encryption_set.des.id # SC-13: Customer-managed keys for enhanced encryption control

  tags = merge(var.tags, {
    "Purpose" = "Media and larger data storage"
  })

  depends_on = [var.aks_cluster_id, null_resource.wait_for_disk_encryption_set_permissions]
}

# Create a Recovery Services Vault for backup
resource "azurerm_data_protection_backup_vault" "disk_backup_vault" {
  name                = var.disk_backup_vault_name
  resource_group_name = var.resource_group_name
  location            = var.location
  datastore_type      = "VaultStore"
  redundancy          = "LocallyRedundant"

  identity {
    type = "SystemAssigned"
  }
}

data "azurerm_data_protection_backup_vault" "disk_backup_vault" {
  name                = azurerm_data_protection_backup_vault.disk_backup_vault.name
  resource_group_name = var.resource_group_name
}

# Create a backup policy for the managed disks
resource "azurerm_data_protection_backup_policy_disk" "disk_backup_policy" {
  name     = var.disk_backup_policy_name
  vault_id = azurerm_data_protection_backup_vault.disk_backup_vault.id

  backup_repeating_time_intervals = ["R/2021-05-19T04:00:00+00:00/P1D"]
  default_retention_duration      = "P7D"

  retention_rule {
    name     = "Weekly"
    duration = "P7D"
    priority = 20
    criteria {
      absolute_criteria = "FirstOfWeek"
    }
  }
}

resource "azurerm_role_assignment" "ssd_backup_vault_disk_backup_contributor" {
  scope                = azurerm_managed_disk.aks_ssd_disk.id
  role_definition_name = "Disk Backup Reader"
  principal_id         = data.azurerm_data_protection_backup_vault.disk_backup_vault.identity[0].principal_id
}

resource "azurerm_role_assignment" "hdd_backup_vault_disk_backup_contributor" {
  scope                = azurerm_managed_disk.aks_hdd_disk.id
  role_definition_name = "Disk Backup Reader"
  principal_id         = data.azurerm_data_protection_backup_vault.disk_backup_vault.identity[0].principal_id
}

resource "azurerm_role_assignment" "ssd_backup_vault_disk_snapshot_contributor" {
  scope                = azurerm_managed_disk.aks_ssd_disk.id
  role_definition_name = "Disk Snapshot Contributor"
  principal_id         = data.azurerm_data_protection_backup_vault.disk_backup_vault.identity[0].principal_id
}

resource "azurerm_role_assignment" "hdd_backup_vault_disk_snapshot_contributor" {
  scope                = azurerm_managed_disk.aks_hdd_disk.id
  role_definition_name = "Disk Snapshot Contributor"
  principal_id         = data.azurerm_data_protection_backup_vault.disk_backup_vault.identity[0].principal_id
}

resource "azurerm_role_assignment" "backup_vault_key_vault_crypto_user" {
  scope                = var.keyvault_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = data.azurerm_data_protection_backup_vault.disk_backup_vault.identity[0].principal_id
}

resource "time_sleep" "wait_30_seconds" {
  depends_on = [
    azurerm_data_protection_backup_vault.disk_backup_vault,
    azurerm_role_assignment.ssd_backup_vault_disk_backup_contributor,
    azurerm_role_assignment.hdd_backup_vault_disk_backup_contributor,
    azurerm_role_assignment.ssd_backup_vault_disk_snapshot_contributor,
    azurerm_role_assignment.hdd_backup_vault_disk_snapshot_contributor,
    azurerm_role_assignment.backup_vault_key_vault_crypto_user,
    azurerm_data_protection_backup_policy_disk.disk_backup_policy
  ]
  create_duration = "30s"
}

# Protect the SSD managed disk with the backup policy
resource "azurerm_data_protection_backup_instance_disk" "ssd_disk_backup" {
  name                         = "ssd-disk-backup"
  location                     = var.location
  vault_id                     = azurerm_data_protection_backup_vault.disk_backup_vault.id
  disk_id                      = azurerm_managed_disk.aks_ssd_disk.id
  snapshot_resource_group_name = var.resource_group_name
  backup_policy_id             = azurerm_data_protection_backup_policy_disk.disk_backup_policy.id

  depends_on = [
    time_sleep.wait_30_seconds,
    azurerm_role_assignment.ssd_backup_vault_disk_backup_contributor,
    azurerm_role_assignment.ssd_backup_vault_disk_snapshot_contributor,
    azurerm_role_assignment.backup_vault_key_vault_crypto_user,
    azurerm_managed_disk.aks_ssd_disk,
    azurerm_data_protection_backup_vault.disk_backup_vault,
    azurerm_data_protection_backup_policy_disk.disk_backup_policy
  ]
}

# Protect the HDD managed disk with the backup policy
resource "azurerm_data_protection_backup_instance_disk" "hdd_disk_backup" {
  name                         = "hdd-disk-backup"
  location                     = var.location
  vault_id                     = azurerm_data_protection_backup_vault.disk_backup_vault.id
  disk_id                      = azurerm_managed_disk.aks_hdd_disk.id
  snapshot_resource_group_name = var.resource_group_name
  backup_policy_id             = azurerm_data_protection_backup_policy_disk.disk_backup_policy.id

  depends_on = [
    time_sleep.wait_30_seconds,
    azurerm_role_assignment.ssd_backup_vault_disk_backup_contributor,
    azurerm_role_assignment.ssd_backup_vault_disk_snapshot_contributor,
    azurerm_role_assignment.backup_vault_key_vault_crypto_user,
    azurerm_managed_disk.aks_ssd_disk,
    azurerm_data_protection_backup_vault.disk_backup_vault,
    azurerm_data_protection_backup_policy_disk.disk_backup_policy
  ]
}
