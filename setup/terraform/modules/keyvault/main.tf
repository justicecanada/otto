data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "kv" {
  name                       = var.keyvault_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 7

  tags = var.tags
}

resource "azurerm_role_assignment" "kv_role" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = var.admin_group_object_id
}

resource "azurerm_role_assignment" "current_user_kv_role" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Wait 5 minutes to allow the permissions to propagate
resource "null_resource" "wait_for_permission_propagation" {
  provisioner "local-exec" {
    command = "sleep 300"
  }
  depends_on = [
    azurerm_role_assignment.kv_role,
    azurerm_role_assignment.current_user_kv_role
  ]
}

resource "azurerm_key_vault_key" "cmk" {
  name         = "otto-encryption-key"
  key_vault_id = azurerm_key_vault.kv.id
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
  depends_on = [null_resource.wait_for_permission_propagation]
}

resource "azurerm_key_vault_secret" "entra_client_secret" {
  name         = "ENTRA-CLIENT-SECRET"
  value        = var.entra_client_secret
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [null_resource.wait_for_permission_propagation]
}

resource "random_password" "vectordb_password" {
  length  = 16
  special = true
}

resource "azurerm_key_vault_secret" "vectordb_password" {
  name         = "VECTORDB-PASSWORD"
  value        = random_password.vectordb_password.result
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [null_resource.wait_for_permission_propagation]
}

resource "random_password" "django_secret_key" {
  length = 50
}

resource "azurerm_key_vault_secret" "django_secret_key" {
  name         = "DJANGO-SECRET-KEY"
  value        = random_password.django_secret_key.result
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [null_resource.wait_for_permission_propagation]
}
