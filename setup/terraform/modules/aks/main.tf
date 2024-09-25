# AU-4(1): Create a Log Analytics workspace
resource "azurerm_log_analytics_workspace" "aks" {
  name                = "${var.aks_cluster_name}-logs"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

# Define the Azure Kubernetes Service (AKS) cluster
resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.aks_cluster_name
  location            = var.location
  resource_group_name = var.resource_group_name
  kubernetes_version  = "1.29.7"
  dns_prefix          = var.aks_cluster_name

  ## TODO: Configure the private cluster settings
  #private_cluster_enabled = true
  #private_dns_zone_id     = "System" # Consider a custom DNS zone instead

  # Configure the default node pool
  default_node_pool {
    name       = "default"
    node_count = 1
    vm_size    = "Standard_DS2_v2"

    # Set upgrade settings for the node pool
    upgrade_settings {
      max_surge = "10%"
    }
  }

  # Set the identity type to SystemAssigned
  identity {
    type = "SystemAssigned"
  }

  # SC-12 & SC-13: Enabling Azure Key Vault secrets provider for secure key management
  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "2m"
  }

  # SC-8: Secure Internal Communication in AKS
  # CM-8(3): Network Policies for AKS
  network_profile {
    network_plugin    = "kubenet"
    load_balancer_sku = "standard"
  }

  oms_agent {
    # AU-6: Enables automated analysis and reporting capabilities
    # CM-8(3): For detecting unauthorized components or suspicious activities
    log_analytics_workspace_id = azurerm_log_analytics_workspace.aks.id
  }

  automatic_channel_upgrade = "stable"

  maintenance_window {
    allowed {
      day   = "Sunday"
      hours = [21, 22, 23, 0]
    }
  }

  # CM-8(3): Azure Active Directory integration and RBAC can be used to enforce compliance and detect unauthorized access attempts
  azure_active_directory_role_based_access_control {
    managed                = true # Deprecated but still required
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }

  local_account_disabled = true

  disk_encryption_set_id = var.disk_encryption_set_id

  # Set resource tags
  tags = var.tags

  # Specify dependencies
  depends_on = [var.acr_id]
}

resource "azurerm_role_assignment" "aks_des_reader" {
  scope                = var.disk_encryption_set_id
  role_definition_name = "Reader"
  principal_id         = azurerm_kubernetes_cluster.aks.identity[0].principal_id

  depends_on = [azurerm_kubernetes_cluster.aks]
}

resource "azurerm_role_assignment" "aks_vm_contributor" {
  scope                = var.disk_encryption_set_id
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = azurerm_kubernetes_cluster.aks.identity[0].principal_id

  depends_on = [azurerm_kubernetes_cluster.aks]
}

resource "azurerm_role_assignment" "rbac_cluster_admin" {
  for_each             = toset(var.admin_group_object_ids)
  principal_id         = each.value
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  scope                = azurerm_kubernetes_cluster.aks.id
}

resource "azurerm_role_assignment" "kv_secrets_provider_user" {
  # SC-12: RBAC for AKS to access Key Vault secrets
  principal_id         = azurerm_kubernetes_cluster.aks.key_vault_secrets_provider[0].secret_identity[0].object_id
  role_definition_name = "Key Vault Secrets User"
  scope                = var.keyvault_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "acr_pull" {
  principal_id         = azurerm_kubernetes_cluster.aks.identity[0].principal_id
  role_definition_name = "AcrPull"
  scope                = var.acr_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "acr_pull_kubelet" {
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name = "AcrPull"
  scope                = var.acr_id
  principal_type       = "ServicePrincipal"
}

# AU-4(1): AKS cluster is configured to use Azure Monitor for logging
# AU-6: Comprehensive audit logging
# AU-7: Integration with Azure Monitor provides audit reduction and report generation capabilities
resource "azurerm_monitor_diagnostic_setting" "aks" {
  name               = "${var.aks_cluster_name}-diagnostics"
  target_resource_id = azurerm_kubernetes_cluster.aks.id

  # AU-7: Ensures that original audit records are preserved
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.aks.id
  log_analytics_destination_type = "Dedicated"

  # AU-4(1): Send logs to a storage account
  storage_account_id = var.storage_account_id

  # AU-4(1) & AU-7: Enable the required logs and metrics
  enabled_log {
    category = "kube-apiserver"
  }

  enabled_log {
    category = "kube-audit"
  }

  enabled_log {
    category = "kube-audit-admin"
  }

  enabled_log {
    category = "kube-controller-manager"
  }

  enabled_log {
    category = "kube-scheduler"
  }

  enabled_log {
    category = "cluster-autoscaler"
  }

  enabled_log {
    category = "guard"
  }

  metric {
    category = "AllMetrics"
  }
}
