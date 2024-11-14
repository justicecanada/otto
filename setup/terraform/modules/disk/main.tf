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
  tags                = var.tags

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
