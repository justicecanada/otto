# SSD for static files and performance-sensitive data
resource "azurerm_managed_disk" "aks_ssd_disk" {
  name                 = "${var.disk_name}-ssd"
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_type = "Premium_LRS"  # Or "StandardSSD_LRS" depending on your needs
  create_option        = "Empty"
  disk_size_gb         = var.ssd_disk_size  # Smaller size, e.g., 32 or 64 GB
  os_type              = "Linux"

  public_network_access_enabled = false
  disk_encryption_set_id        = null

  tags = merge(var.tags, {
    "Purpose" = "Static files and performance-sensitive data"
  })

  depends_on = [var.aks_cluster_id]
}

# HDD for larger, less frequently accessed data
resource "azurerm_managed_disk" "aks_hdd_disk" {
  name                 = "${var.disk_name}-hdd"
  location             = var.location
  resource_group_name  = var.resource_group_name
  storage_account_type = "Standard_LRS"  # HDD storage
  create_option        = "Empty"
  disk_size_gb         = var.hdd_disk_size  # Larger size, e.g., 500 or 1000 GB
  os_type              = "Linux"

  public_network_access_enabled = false
  disk_encryption_set_id        = null

  tags = merge(var.tags, {
    "Purpose" = "Media and larger data storage"
  })

  depends_on = [var.aks_cluster_id]
}
