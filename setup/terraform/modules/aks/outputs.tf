output "aks_cluster_name" {
  value       = azurerm_kubernetes_cluster.aks.name
  description = "The name of the AKS cluster"
}

output "aks_cluster_id" {
  value       = azurerm_kubernetes_cluster.aks.id
  description = "The ID of the AKS cluster"
}

output "outbound_ip_resource_id" {
  value       = tolist(azurerm_kubernetes_cluster.aks.network_profile[0].load_balancer_profile[0].effective_outbound_ips)[0]
  description = "The resource ID of the outbound IP address of the AKS cluster"
}
