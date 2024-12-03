resource "azurerm_cognitive_account" "cognitive_services" {
  name                = var.name
  location            = var.location # SA-9(5): Store data in a location that complies with data residency requirements
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
