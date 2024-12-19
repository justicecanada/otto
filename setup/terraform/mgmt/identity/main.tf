resource "azurerm_user_assigned_identity" "jumpbox_identity" {
  name                = var.identity_name
  resource_group_name = var.resource_group_name
  location            = var.location

  tags = var.tags
}

# The Owner role, with highest privilege, is required for the VM to create resources in the subscription and manage user access between resources. 
# The Contributor role is not sufficient as it lacks User Access Administrator permissions. The Terraform script will fail without this role assignment.
resource "azurerm_role_assignment" "jumpbox_identity_owner" {
  scope                = azurerm_user_assigned_identity.jumpbox_identity.id
  role_definition_name = "Owner"
  principal_id         = data.azurerm_client_config.current.object_id
}

data "azurerm_client_config" "current" {}