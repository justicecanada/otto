resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.aks_cluster_name
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_prefix          = var.aks_cluster_name

  default_node_pool {
    name       = "default"
    node_count = 1
    vm_size    = "Standard_DS2_v2"

    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags

  depends_on = [var.acr_id]

}

resource "azurerm_role_assignment" "rbac_cluster_admin" {
  principal_id         = var.admin_group_object_id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  scope                = azurerm_kubernetes_cluster.aks.id
}

resource "azurerm_role_assignment" "admin_kv_secrets_user" {
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].client_id
  role_definition_name = "Key Vault Secrets User"
  scope                = var.admin_keyvault_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "kv_secrets_user" {
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].client_id
  role_definition_name = "Key Vault Secrets User"
  scope                = var.keyvault_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "acr_pull" {
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].client_id
  role_definition_name = "AcrPull"
  scope                = var.acr_id
  principal_type       = "ServicePrincipal"
}
