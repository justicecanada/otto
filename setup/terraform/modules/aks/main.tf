# AU-4(1): Create a Log Analytics workspace
resource "azurerm_log_analytics_workspace" "aks" {
  name                = "${var.aks_cluster_name}-logs"
  location            = var.location # SA-9(5): Store data in a location that complies with data residency requirements
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

locals {
  max_node_count = floor(var.approved_cpu_quota / var.vm_cpu_count)
}

# Define the Azure Kubernetes Service (AKS) cluster
resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.aks_cluster_name
  location            = var.location
  resource_group_name = var.resource_group_name
  kubernetes_version  = "1.29.7"
  dns_prefix          = var.aks_cluster_name

  oidc_issuer_enabled       = true # OIDC issuer is enabled for AKS cluster authentication with Azure AD
  workload_identity_enabled = true # Workload identity allows the AKS cluster to use managed identities for Azure resources

  # AC-22, IA-8, SC-2, SC-5: Configure the private cluster settings
  # TODO: Uncomment when SSC routes all traffic to the VNET through ExpressRoute
  #private_cluster_enabled = var.use_private_network

  # Cluster-level autoscaling configuration:
  # - vm_size: Defines CPU and memory for each node (e.g., "Standard_D4s_v3")
  # - min_count and max_count: Set lower and upper bounds for node count
  # - enable_auto_scaling: Activates autoscaler for this node pool
  #
  # Autoscaler adds nodes when pods can't be scheduled due to resource constraints,
  # and removes underutilized nodes when pods can be rescheduled.
  #
  # Fine-tune behavior with auto_scaler_profile settings in AKS cluster resource.
  # Pod-level autoscaling: See HorizontalPodAutoscaler in K8s manifests.
  # Container resource limits: Defined in individual deployment files.

  default_node_pool {
    name                = "default"
    vm_size             = var.vm_size
    enable_auto_scaling = true
    min_count           = 1
    max_count           = local.max_node_count
    vnet_subnet_id      = var.web_subnet_id

    upgrade_settings {
      max_surge = "10%" # Max nodes that can be added during an upgrade
    }
  }

  auto_scaler_profile {
    balance_similar_node_groups      = true     # Attempts to balance the size of similarly labeled node groups
    expander                         = "random" # Chooses a random node group when scaling out
    max_graceful_termination_sec     = 600      # Maximum time to wait for pod termination when scaling down (10 minutes)
    max_node_provisioning_time       = "15m"    # Maximum time to wait for a node to be provisioned
    max_unready_nodes                = 3        # Maximum number of unready nodes before affecting cluster operations
    max_unready_percentage           = 45       # Maximum percentage of unready nodes before affecting cluster operations
    new_pod_scale_up_delay           = "10s"    # Delay before scaling up for newly created pods
    scale_down_delay_after_add       = "10m"    # Wait time after adding nodes before considering scale down
    scale_down_delay_after_delete    = "10s"    # Wait time after deleting nodes before considering further scale down
    scale_down_delay_after_failure   = "3m"     # Wait time after a failed scale down before retrying
    scan_interval                    = "10s"    # How often the autoscaler checks the cluster state
    scale_down_unneeded              = "10m"    # How long a node should be unneeded before it's considered for scale down
    scale_down_unready               = "20m"    # How long an unready node should be unneeded before it's considered for scale down
    scale_down_utilization_threshold = 0.5      # Node utilization level below which it's considered for scale down (50%)
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
  # AC-3 & CM-8(3): Network Policies for AKS
  network_profile {
    network_plugin = "kubenet" # Azure CNI provides integration with Azure networking features; Kubenet is simpler and will suffice in Sandbox; TODO: Change to "azure" after resolving issues.
    #network_policy    = "azure"    # Azure network policies control traffic flow between pods; TODO: Enable network policy once rules are configured to work in environment.
    load_balancer_sku = "standard" # Standard SKU provides more features and better performance
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

  # AC-3 & CM-8(3): Azure Active Directory integration and RBAC can be used to enforce compliance and detect unauthorized access attempts
  # AC-3(7): Use Azure AD groups for role assignments and permission management in AKSs
  # AC-20, AC-20(3), SC-2: AAD enables centralized identity management and access control
  azure_active_directory_role_based_access_control {
    managed                = true # Deprecated but still required
    azure_rbac_enabled     = true # AC-22: Enable Azure RBAC
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

# This role assignment grants the AKS kubelet identity access to blob storage
resource "azurerm_role_assignment" "aks_storage_blob_data_contributor" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  depends_on           = [azurerm_kubernetes_cluster.aks]
}

resource "azurerm_role_assignment" "aks_log_analytics_reader" {
  for_each             = toset(var.log_analytics_readers_group_object_ids)
  principal_id         = each.value
  role_definition_name = "Log Analytics Reader"
  scope                = azurerm_log_analytics_workspace.aks.id
  depends_on           = [azurerm_kubernetes_cluster.aks]
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

locals { admin_email_list = split(",", var.admin_email) }

# SC-5(3): Create an action group for AKS alerts
resource "azurerm_monitor_action_group" "aks_alerts" {
  name                = "${var.aks_cluster_name}-alert-group"
  resource_group_name = var.resource_group_name
  short_name          = "aksalerts"
  tags                = var.tags

  dynamic "email_receiver" {
    for_each = local.admin_email_list
    content {
      name                    = "admin${index(local.admin_email_list, email_receiver.value) + 1}"
      email_address           = trimspace(email_receiver.value)
      use_common_alert_schema = true
    }
  }
}

# SC-5, SC-5(3): Network traffic spike alert: Notifies when network traffic reaches an abnormally high level
resource "azurerm_monitor_metric_alert" "aks_network_alert" {
  name                = "${var.aks_cluster_name}-network-spike-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_kubernetes_cluster.aks.id]
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.ContainerService/managedClusters"
    metric_name      = "node_network_in_bytes"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 1000000000 # 1 GB, adjust as needed
  }

  action {
    action_group_id = azurerm_monitor_action_group.aks_alerts.id
  }

  frequency   = "PT1M"
  window_size = "PT5M"
}


# SC-5, SC-5(3): CPU usage alert: Notifies when CPU reaches an abnormally high level, which could be caused by a DDoS attack
resource "azurerm_monitor_metric_alert" "aks_cpu_alert" {
  name                = "${var.aks_cluster_name}-high-cpu-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_kubernetes_cluster.aks.id]
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.ContainerService/managedClusters"
    metric_name      = "node_cpu_usage_percentage"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80 # Adjust based on your normal CPU usage
  }

  action {
    action_group_id = azurerm_monitor_action_group.aks_alerts.id
  }
}

# SC-5, SC-5(3): Request rate alert: Notifies when the request rate is abnormally high
resource "azurerm_monitor_metric_alert" "aks_request_rate_alert" {
  name                = "${var.aks_cluster_name}-high-request-rate-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_kubernetes_cluster.aks.id]
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.ContainerService/managedClusters"
    metric_name      = "kube_pod_status_ready"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 1000 # Adjust based on your normal traffic
  }

  action {
    action_group_id = azurerm_monitor_action_group.aks_alerts.id
  }
}

# SC-5, SC-5(3): Connection count alert: Notifies when the number of connections to the cluster is abnormally high
resource "azurerm_monitor_metric_alert" "aks_connection_count_alert" {
  name                = "${var.aks_cluster_name}-high-connection-count-alert"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_kubernetes_cluster.aks.id]
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.ContainerService/managedClusters"
    metric_name      = "kube_node_status_condition"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 5000 # Adjust based on your expected connection limits
  }

  action {
    action_group_id = azurerm_monitor_action_group.aks_alerts.id
  }
}

# AC-6(10): Restricting privileged function
# AU-6: Audit review, analysis, and reporting
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "privileged_access_denied_alert" {
  name                 = "${var.aks_cluster_name}-privileged-access-denied-alert"
  resource_group_name  = var.resource_group_name
  location             = var.location
  description          = "Alerts when privileged access is denied in the Django application"
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"
  scopes               = [azurerm_kubernetes_cluster.aks.id]
  tags                 = var.tags
  severity             = 0 # Severe Alert
  enabled              = true

  criteria {
    query                   = <<QUERY
      ContainerLogV2
      | where ContainerName == "django-app-container" 
        and LogMessage.admin == "true" 
        and LogMessage.category == "security" 
        and LogMessage.event == "User does not have permission"        
      | summarize Count = count()
    QUERY
    time_aggregation_method = "Total"
    operator                = "GreaterThan"
    threshold               = 0
    metric_measure_column   = "Count"

    failing_periods {
      number_of_evaluation_periods             = 1
      minimum_failing_periods_to_trigger_alert = 1
    }
  }

  auto_mitigation_enabled = true

  action {
    action_groups = [azurerm_monitor_action_group.aks_alerts.id]
  }

}

# AC-6(9): Least privilege
# AU-6: Audit review, analysis, and reporting
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "privileged_access_alert" {
  name                 = "${var.aks_cluster_name}-privileged-access-alert"
  resource_group_name  = var.resource_group_name
  location             = var.location
  description          = "Alerts when an authorized user executes a privileged function in the Django application"
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"
  scopes               = [azurerm_kubernetes_cluster.aks.id]
  tags                 = var.tags
  severity             = 3 # Informational Alert
  enabled              = true

  criteria {
    query                   = <<QUERY
      ContainerLogV2
      | where ContainerName == "django-app-container" 
      and LogMessage.admin == "true" 
      and LogMessage.category == "security" 
      and LogMessage.event <> "User does not have permission"
      | summarize Count = count()
    QUERY
    time_aggregation_method = "Total"
    operator                = "GreaterThan"
    threshold               = 0
    metric_measure_column   = "Count"

    failing_periods {
      number_of_evaluation_periods             = 1
      minimum_failing_periods_to_trigger_alert = 1
    }
  }

  auto_mitigation_enabled = true

  action {
    action_groups = [azurerm_monitor_action_group.aks_alerts.id]
  }
}
