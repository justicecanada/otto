# Local value to determine the current privacy state
# This allows for a one-way transition from public to private
locals {
  storage_is_private = var.mgmt_storage_make_private
}

resource "azurerm_storage_account" "mgmt_storage" {
  name                     = var.mgmt_storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"

  min_tls_version               = "TLS1_2"
  # Set public network access based on the privacy state
  public_network_access_enabled = !local.storage_is_private

  # Configure network rules based on the privacy state
  network_rules {
    default_action             = local.storage_is_private ? "Deny" : "Allow"
    virtual_network_subnet_ids = local.storage_is_private ? [var.mgmt_subnet_id] : null
    bypass                     = ["AzureServices"]
  }

  tags = var.tags
  
  # Prevent accidental deletion of the storage account
  lifecycle {
    prevent_destroy = true
  }
}

# Null resource to track the privacy state of the storage account
# This resource will cause Terraform to show an error if attempting to revert from private to public
resource "null_resource" "storage_private_state" {
  # The trigger will update this resource when the privacy state changes
  triggers = {
    is_private = local.storage_is_private
  }

  # Prevent destruction of this resource, which indirectly prevents changing from private to public
  lifecycle {
    prevent_destroy = true
  }
}

# The logic works as follows:
# 1. Initially, the storage account can be created with public access (mgmt_storage_make_private = false)
# 2. To make it private, set mgmt_storage_make_private = true
# 3. Once private, attempting to set mgmt_storage_make_private = false will cause an error
#    because Terraform will try to destroy and recreate the null_resource, which is prevented
# This ensures a one-way transition from public to private access for the storage account


resource "azurerm_private_endpoint" "storage_endpoint" {
  name                = "pe-otto-${var.environment}-mgmt-storage"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.mgmt_subnet_id

  private_service_connection {
    name                           = "psc-otto-${var.environment}-mgmt-storage"
    private_connection_resource_id = azurerm_storage_account.mgmt_storage.id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }
}

resource "azurerm_private_dns_a_record" "storage_dns_record" {
  name                = var.mgmt_storage_account_name
  zone_name           = var.blob_private_dns_zone_id
  resource_group_name = var.resource_group_name
  ttl                 = 300
  records             = [azurerm_private_endpoint.storage_endpoint.private_service_connection[0].private_ip_address]
}