# Add a delay to allow for the key vault's purge protection to be enabled 
resource "null_resource" "wait_for_purge_protection" {
  provisioner "local-exec" {
    command = "sleep 120"
  }
  depends_on = [var.wait_for_propagation, var.cmk_id]
}

resource "azurerm_disk_access" "disk_access" {
  name                = "disk-access-${var.disk_name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
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
  name                          = "${var.disk_name}-ssd"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  storage_account_type          = "Premium_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = var.ssd_disk_size
  os_type                       = "Linux"
  public_network_access_enabled = false
  network_access_policy         = "AllowPrivate"
  disk_access_id                = azurerm_disk_access.disk_access.id
  disk_encryption_set_id        = azurerm_disk_encryption_set.des.id # SC-28 & SC-28(1): Customer-managed keys for enhanced encryption control

  tags = merge(var.tags, {
    "Purpose" = "Static files and performance-sensitive data"
  })

  depends_on = [var.aks_cluster_id, null_resource.wait_for_disk_encryption_set_permissions]
}

# HDD for larger, less frequently accessed data
resource "azurerm_managed_disk" "aks_hdd_disk" {
  name                          = "${var.disk_name}-hdd"
  location                      = var.location
  resource_group_name           = var.resource_group_name
  storage_account_type          = "Standard_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = var.hdd_disk_size
  os_type                       = "Linux"
  public_network_access_enabled = false
  network_access_policy         = "AllowPrivate"
  disk_access_id                = azurerm_disk_access.disk_access.id
  disk_encryption_set_id        = azurerm_disk_encryption_set.des.id # SC-13: Customer-managed keys for enhanced encryption control

  tags = merge(var.tags, {
    "Purpose" = "Media and larger data storage"
  })

  depends_on = [var.aks_cluster_id, null_resource.wait_for_disk_encryption_set_permissions]
}

# Create a private endpoint for each disk
resource "azurerm_private_endpoint" "disk_access_endpoint" {
  name                = "${var.disk_name}-disk-access-endpoint"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.app_subnet_id

  private_service_connection {
    name                           = "${var.disk_name}-disk-access-privateserviceconnection"
    private_connection_resource_id = azurerm_disk_access.disk_access.id
    is_manual_connection           = false
    subresource_names              = ["disks"]
  }

  tags = var.tags
}

# Create a Network Security Group (NSG) to control access to the disks
resource "azurerm_network_security_group" "disk_nsg" {
  name                = "${var.disk_name}-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name

  security_rule {
    name                       = "AllowCorporateIP"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "${var.corporate_ip}/32"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowWebSubnet"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = var.web_subnet_address_prefix
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowAppSubnet"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = var.app_subnet_address_prefix
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowDbSubnet"
    priority                   = 130
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = var.db_subnet_address_prefix
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = var.tags
}

# Associate the NSG with the subnet where the private endpoints are located
resource "azurerm_subnet_network_security_group_association" "disk_nsg_association" {
  subnet_id                 = var.app_subnet_id
  network_security_group_id = azurerm_network_security_group.disk_nsg.id
}
