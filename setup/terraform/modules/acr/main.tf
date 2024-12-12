# CM-8: ACR maintains an inventory of container images used in the system
resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = var.resource_group_name
  location            = var.location # SA-9(5): Store data in a location that complies with data residency requirements
  sku                 = var.acr_sku

  admin_enabled = true

  tags = var.tags

  depends_on = [var.keyvault_id]
}

# Role assignment for ACR
resource "azurerm_role_assignment" "acr_push" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPush"
  principal_id         = var.jumpbox_identity_id

  depends_on = [azurerm_container_registry.acr]
}
