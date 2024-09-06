resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.acr_sku

  admin_enabled = true

  tags = var.tags

  depends_on = [var.keyvault_id]
}

# Role assignment for ACR
resource "azurerm_role_assignment" "acr_push" {
  for_each             = toset(var.acr_publisher_object_ids)
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPush"
  principal_id         = each.value

  depends_on = [azurerm_container_registry.acr]
}
