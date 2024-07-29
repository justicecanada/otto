resource "azurerm_cognitive_account" "cognitive_services" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  kind                = "CognitiveServices"
  sku_name            = "S0"

  tags = var.tags

  depends_on = [var.keyvault_id]
}

resource "azurerm_key_vault_secret" "cognitive_services_key" {
  name         = "COGNITIVE-SERVICE-KEY"
  value        = azurerm_cognitive_account.cognitive_services.primary_access_key
  key_vault_id = var.keyvault_id
}

