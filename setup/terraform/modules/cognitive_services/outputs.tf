output "id" {
  value       = azurerm_cognitive_account.cognitive_services.id
  description = "The ID of the Cognitive Services account"
}

output "endpoint" {
  value       = azurerm_cognitive_account.cognitive_services.endpoint
  description = "The endpoint used to connect to the Cognitive Services account"
}

output "primary_access_key" {
  value       = azurerm_cognitive_account.cognitive_services.primary_access_key
  description = "The primary access key for the Cognitive Services account"
  sensitive   = true
}

output "secondary_access_key" {
  value       = azurerm_cognitive_account.cognitive_services.secondary_access_key
  description = "The secondary access key for the Cognitive Services account"
  sensitive   = true
}
