data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "admin_kv" {
  name                       = var.admin_keyvault_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true  # Enable RBAC-based access control

  tags = var.tags
}

resource "azurerm_key_vault" "kv" {
  name                       = var.keyvault_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true  # Enable RBAC-based access control

  tags = var.tags
}

resource "azurerm_role_assignment" "admin_kv_role" {
  scope                = azurerm_key_vault.admin_kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = var.admin_group_object_id
}

resource "azurerm_role_assignment" "kv_role" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = var.admin_group_object_id
}

# Add role assignment for the current user/service principal
resource "azurerm_role_assignment" "current_user_admin_kv_role" {
  scope                = azurerm_key_vault.admin_kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "current_user_kv_role" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

# # Sometimes adding the "Key Vault Reader" role helps
# resource "azurerm_role_assignment" "current_user_admin_kv_reader_role" {
#   scope                = azurerm_key_vault.admin_kv.id
#   role_definition_name = "Key Vault Reader"
#   principal_id         = data.azurerm_client_config.current.object_id
# }

# resource "azurerm_role_assignment" "current_user_kv_reader_role" {
#   scope                = azurerm_key_vault.kv.id
#   role_definition_name = "Key Vault Reader"
#   principal_id         = data.azurerm_client_config.current.object_id
# }

# Add a time delay to allow for role propagation
resource "time_sleep" "wait_30_seconds" {
  depends_on = [
    azurerm_role_assignment.admin_kv_role,
    azurerm_role_assignment.kv_role,
    azurerm_role_assignment.current_user_admin_kv_role,
    azurerm_role_assignment.current_user_kv_role
  ]

  create_duration = "30s"
}

# This null_resource depends on the time_sleep resource, ensuring that
# subsequent resources wait for the roles to propagate
resource "null_resource" "next_step" {
  depends_on = [time_sleep.wait_30_seconds]
}
