data "azurerm_subscription" "current" {}

# Define the custom role for Velero
resource "azurerm_role_definition" "velero" {
  name        = "Velero"
  scope       = data.azurerm_subscription.current.id
  description = "Velero related permissions to perform backups, restores and deletions"

  permissions {
    actions = [
      "Microsoft.Compute/disks/read",
      "Microsoft.Compute/disks/write",
      "Microsoft.Compute/disks/endGetAccess/action",
      "Microsoft.Compute/disks/beginGetAccess/action",
      "Microsoft.Compute/snapshots/read",
      "Microsoft.Compute/snapshots/write",
      "Microsoft.Compute/snapshots/delete",
      "Microsoft.Storage/storageAccounts/listkeys/action",
      "Microsoft.Storage/storageAccounts/regeneratekey/action",
      "Microsoft.Storage/storageAccounts/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/delete",
      "Microsoft.Storage/storageAccounts/blobServices/containers/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/write",
      "Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action"
    ]
    data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/move/action",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action"
    ]
  }

  assignable_scopes = [
    data.azurerm_subscription.current.id
  ]
}

# Create a managed identity for Velero
resource "azurerm_user_assigned_identity" "velero" {
  resource_group_name = var.resource_group_name
  location            = var.location
  name                = "velero"
}

# Role assignment for Velero
resource "azurerm_role_assignment" "velero" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = azurerm_role_definition.velero.name
  principal_id         = azurerm_user_assigned_identity.velero.principal_id
}

# Create federated identity credential
resource "azurerm_federated_identity_credential" "velero" {
  name                = "kubernetes-federated-credential"
  resource_group_name = var.resource_group_name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = var.oidc_issuer_url
  parent_id           = azurerm_user_assigned_identity.velero.id
  subject             = "system:serviceaccount:velero:velero"
}
