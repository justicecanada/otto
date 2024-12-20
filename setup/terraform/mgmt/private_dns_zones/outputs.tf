output "keyvault_private_dns_zone_id" {
  value       = azurerm_private_dns_zone.keyvault_zone.id
  description = "The ID of the Key Vault private DNS zone"
}

output "blob_private_dns_zone_id" {
  value       = azurerm_private_dns_zone.blob_zone.id
  description = "The ID of the Blob Storage private DNS zone"
}

output "aks_private_dns_zone_id" {
  value       = azurerm_private_dns_zone.aks_dns.id
  description = "The ID of the AKS private DNS zone"
}

output "private_dns_zone_ids" {
  value = {
    keyvault = azurerm_private_dns_zone.keyvault_zone.id
    blob     = azurerm_private_dns_zone.blob_zone.id
    aks      = azurerm_private_dns_zone.aks_dns.id
  }
  description = "A map of private DNS zone IDs"
}
