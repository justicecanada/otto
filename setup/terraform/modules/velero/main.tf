# data "azurerm_subscription" "current" {}

# # Create a managed identity for Velero
# resource "azurerm_user_assigned_identity" "velero" {
#   resource_group_name = var.resource_group_name
#   location            = var.location
#   name                = var.velero_identity_name
# }

# # Assign the Contributor role to the managed identity
# resource "azurerm_role_assignment" "velero_storage_contributor" {
#   scope                = var.storage_account_id
#   role_definition_name = "Contributor"
#   principal_id         = azurerm_user_assigned_identity.velero.principal_id
#   depends_on           = [azurerm_user_assigned_identity.velero]
# }

# # Create federated identity credential
# resource "azurerm_federated_identity_credential" "velero" {
#   name                = "kubernetes-federated-credential"
#   resource_group_name = var.resource_group_name
#   audience            = ["api://AzureADTokenExchange"]
#   issuer              = var.oidc_issuer_url
#   parent_id           = azurerm_user_assigned_identity.velero.id
#   subject             = "system:serviceaccount:velero:velero"
# }
