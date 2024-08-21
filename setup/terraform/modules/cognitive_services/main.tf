resource "azurerm_cognitive_account" "cognitive_services" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  kind                = "CognitiveServices"
  sku_name            = "S0"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags

  depends_on = [var.keyvault_id]
}

resource "azurerm_role_assignment" "cognitive_services_blob_contributor" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_cognitive_account.cognitive_services.identity[0].principal_id
  depends_on           = [azurerm_cognitive_account.cognitive_services]
}

resource "azurerm_key_vault_secret" "cognitive_services_key" {
  name         = "COGNITIVE-SERVICE-KEY"
  value        = azurerm_cognitive_account.cognitive_services.primary_access_key
  key_vault_id = var.keyvault_id
  depends_on   = [azurerm_cognitive_account.cognitive_services, var.wait_for_propagation]
}

