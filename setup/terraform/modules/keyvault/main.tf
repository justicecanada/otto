data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  # SC-12: Centralized key management system
  name                       = var.keyvault_name
  location                   = var.location # SA-9(5): Store data in a location that complies with data residency requirements
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "premium" # SC-13: Premium SKU is FIPS 140-2 Level 2 compliant
  enable_rbac_authorization  = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 7

  public_network_access_enabled = !var.use_private_network

  network_acls {
    default_action             = var.use_private_network ? "Deny" : "Allow"
    bypass                     = "AzureServices"
    virtual_network_subnet_ids = [var.app_subnet_id, var.web_subnet_id] # Allow access from the app, web, and database subnets
  }

  tags = var.tags
}

# TODO: Uncomment when SSC routes all traffic to the VNET through ExpressRoute
# resource "azurerm_private_endpoint" "keyvault" {
#   count               = var.use_private_network ? 1 : 0
#   name                = "${var.keyvault_name}-endpoint"
#   location            = var.location
#   resource_group_name = var.resource_group_name
#   subnet_id           = var.app_subnet_id

#   private_service_connection {
#     name                           = "${var.keyvault_name}-connection"
#     private_connection_resource_id = azurerm_key_vault.kv.id
#     is_manual_connection           = false
#     subresource_names              = ["vault"]
#   }
# }

resource "azurerm_role_assignment" "kv_role" {
  for_each             = toset(var.admin_group_object_ids)
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = each.value
}

# Wait 5 minutes to allow the permissions to propagate
resource "null_resource" "wait_for_permission_propagation" {
  provisioner "local-exec" {
    command = "sleep 300"
  }
  depends_on = [
    azurerm_role_assignment.kv_role
  ]
}

resource "azurerm_key_vault_key" "cmk" {
  # SC-12: Automated key generation and management
  name         = "otto-encryption-key"
  key_vault_id = azurerm_key_vault.kv.id
  key_type     = "RSA" # SC-13: Use RSA keys for encryption
  key_size     = 2048  # SC-13: Use 2048-bit keys for encryption
  key_opts = [
    "decrypt",
    "encrypt",
    "sign",
    "unwrapKey",
    "verify",
    "wrapKey",
  ]
  depends_on = [null_resource.wait_for_permission_propagation]
}
