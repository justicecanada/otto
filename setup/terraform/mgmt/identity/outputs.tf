output "jumpbox_identity_id" {
  value       = azurerm_user_assigned_identity.jumpbox_identity.id
  description = "The ID of the user-assigned managed identity for the management jumpbox"
}