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

# Get the object to the user-defined identity
data "azurerm_user_assigned_identity" "identity" {
  name                = "otto-identity"
  resource_group_name = var.resource_group_name
}

# Create a private DNS zone for AKS
resource "azurerm_private_dns_zone" "aks_dns" {
  name                = "privatelink.canadacentral.azmk8s.io"
  resource_group_name = var.resource_group_name
  
  tags = var.tags
}

# Link the private DNS zone to the virtual network
resource "azurerm_private_dns_zone_virtual_network_link" "aks_dns_link" {
  name                  = "${var.aks_cluster_name}-dns-link"
  resource_group_name   = var.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.aks_dns.name
  virtual_network_id    = var.vnet_id
  registration_enabled  = false # Private DNS zone registration is handled by AKS internally

  tags = var.tags
}

# Get the subnet data for the AKS subnet
data "azurerm_subnet" "web_subnet" {
  name = split("/", var.web_subnet_id)[10]
  virtual_network_name = split("/", var.web_subnet_id)[8]
  resource_group_name = split("/", var.web_subnet_id)[4] 
} 

# NSG for the AKS subnet to allow Inbound on port 443
resource "azurerm_network_security_group" "aks_nsg" {
  name                = "${var.aks_cluster_name}-nsg"
  location            = var.location
  resource_group_name = var.resource_group_name

  security_rule {
    name                       = "AllowAKSInbound443"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "*"
    # Allows HTTPS traffic for secure communication with the Kubernetes API server
  }

  security_rule {
    name                       = "AllowKubeletAPI"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "10250"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Enables communication between the control plane and worker nodes for pod and node management
  }

  security_rule {
    name                       = "AllowKubeScheduler"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "10251"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Allows the Kubernetes scheduler to manage pod scheduling across nodes
  }

  security_rule {
    name                       = "AllowKubeControllerManager"
    priority                   = 130
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "10252"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Enables the Kubernetes controller manager to manage various cluster controllers
  }

  security_rule {
    name                       = "AllowAzureContainerRegistry9000"
    priority                   = 140
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "9000"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Permits outbound traffic to Azure Container Registry for pulling container images
  }

  security_rule {
    name                       = "AllowSSH22"
    priority                   = 150
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Allows SSH access to nodes for debugging and maintenance (optional, use with caution)
  }

  security_rule {
    name                       = "AllowDNS53"
    priority                   = 160
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "53"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Allows DNS traffic for pod and service discovery
  }

  security_rule {
    name                       = "AllowDNSOutbound"
    priority                   = 170
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "53"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Allows outbound DNS traffic for pod and service discovery
  }

  security_rule {
    name                       = "AllowInterNodeInbound"
    priority                   = 180
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows inter-node communication within the AKS cluster
  }

  security_rule {
    name                       = "AllowPostgreSQL"
    priority                   = 190
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5432"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows PostgreSQL traffic between pods in the cluster
  }

  security_rule {
    name                       = "AllowInterNodeOutbound"
    priority                   = 200
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows outbound traffic between nodes for pod communication
  }

  security_rule {
    name                       = "AllowHttpInbound"
    priority                   = 210
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows HTTP traffic between pods for Django service
  }

  security_rule {
    name                       = "AllowHttpsInbound"
    priority                   = 220
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8000"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows HTTPS traffic for Django application
  }

  security_rule {
    name                       = "AllowRedis"
    priority                   = 230
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "6379"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows Redis traffic between pods
  }

  security_rule {
    name                       = "AllowCeleryBeat"
    priority                   = 240
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5555"
    source_address_prefix      = data.azurerm_subnet.web_subnet.address_prefixes[0]
    destination_address_prefix = data.azurerm_subnet.web_subnet.address_prefixes[0]
    # Allows Celery Beat monitoring
  }

  security_rule {
    name                       = "AllowHTTPSOutbound"
    priority                   = 250
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
    # Allows outbound HTTPS traffic for various services and updates
  }

  security_rule {
    name                       = "AllowNTPOutbound"
    priority                   = 260
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "123"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
    # Allows outbound NTP traffic for time synchronization
  }

  security_rule {
    name                       = "AllowAzureCloudOutbound"
    priority                   = 265
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["443", "9000"]
    source_address_prefix      = "*"
    destination_address_prefix = "AzureCloud"
    # Allows outbound traffic to Azure services 
  }

  security_rule {
    name                       = "AllowAzureMonitorOutbound"
    priority                   = 270
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "AzureMonitor"
    # Allows outbound traffic to Azure Monitor
  }

  security_rule {
    name                       = "AllowAzureActiveDirectoryOutbound"
    priority                   = 280
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "AzureActiveDirectory"
    # Allows outbound traffic to Azure Active Directory for authentication
  }

  security_rule {
    name                       = "AllowMCR"
    priority                   = 290
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["443"]
    source_address_prefix      = "*"
    destination_address_prefix = "MicrosoftContainerRegistry"
  }

  security_rule {
    name                       = "AllowDNS"
    priority                   = 1100
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "53"
    source_address_prefix      = "*"
    destination_address_prefix = "168.63.129.16/32"
    # Allow DNS resolution
  }

  tags = var.tags
}

# Associate the NSG with the web subnet
resource "azurerm_subnet_network_security_group_association" "aks_nsg_association" {
  subnet_id                 = var.web_subnet_id
  network_security_group_id = azurerm_network_security_group.aks_nsg.id
}

resource "azurerm_route_table" "aks" {
  name                = "${var.aks_cluster_name}-rt"
  resource_group_name = var.mgmt_resource_group_name
  location            = var.location

  # # Default route to ExpressRoute
  # route {
  #   name                   = "default-route"
  #   address_prefix         = "0.0.0.0/0"
  #   next_hop_type          = "VirtualNetworkGateway"
  # }
  
  route {
    name                   = "default-route"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"
    next_hop_in_ip_address = "10.250.6.4"
  }

  # Direct routes to Azure services
  route {
    name                   = "to-azure-monitor"
    address_prefix         = "AzureMonitor"
    next_hop_type          = "Internet"
  }

  route {
    name                   = "to-azure-active-directory"
    address_prefix         = "AzureActiveDirectory"
    next_hop_type          = "Internet"
  }

  route {
    name                   = "to-azure-container-registry"
    address_prefix         = "AzureContainerRegistry"
    next_hop_type          = "Internet"
  }

  route {
    name                   = "to-mcr"
    address_prefix         = "MicrosoftContainerRegistry"
    next_hop_type          = "Internet"
  }

  tags = var.tags
}
resource "azurerm_subnet_route_table_association" "aks" {
  subnet_id      = var.web_subnet_id
  route_table_id = azurerm_route_table.aks.id
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
  private_cluster_enabled = var.use_private_network
  private_dns_zone_id     = azurerm_private_dns_zone.aks_dns.id

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

  # Assign the identity to the AKS cluster
  identity {
    type         = "UserAssigned"
    identity_ids = [data.azurerm_user_assigned_identity.identity.id]
  }

  # SC-12 & SC-13: Enabling Azure Key Vault secrets provider for secure key management
  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "2m"
  }

  # SC-8: Secure Internal Communication in AKS
  # AC-3 & CM-8(3): Network Policies for AKS
  network_profile {
    # Use kubenet for simplified networking and efficient IP address usage
    # Suitable for clusters with limited IP address space and primarily internal pod communication
    network_plugin = "kubenet"

    # Pod CIDR specifies the IP range from which pod IPs are allocated
    # This range is internal to the cluster and not routable outside
    pod_cidr = var.pod_cidr

    # Service CIDR defines the IP range for internal Kubernetes services
    # Must not overlap with the pod CIDR range and should be a private IP range
    service_cidr = var.service_cidr

    # DNS service IP must be within the service CIDR range
    dns_service_ip = var.dns_service_ip

    # Outbound type determines how outbound traffic is handled
    # "loadBalancer" is the default and recommended for most scenarios
    # "userDefinedRouting" is used when custom routing is required, such as with ExpressRoute
    outbound_type = "userDefinedRouting"
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
    # admin_group_object_ids = var.admin_group_id # TODO: Rethink once the jumpbox VM approach is finalized
  }

  local_account_disabled = true

  disk_encryption_set_id = var.disk_encryption_set_id

  # Set resource tags
  tags = var.tags

  # Specify dependencies
  depends_on = [var.acr_id, azurerm_role_assignment.aks_network_contributor, azurerm_private_dns_zone_virtual_network_link.aks_dns_link]
}

resource "azurerm_role_assignment" "aks_des_reader" {
  scope                = var.disk_encryption_set_id
  role_definition_name = "Reader"
  principal_id         = data.azurerm_user_assigned_identity.identity.principal_id

  depends_on = [azurerm_kubernetes_cluster.aks]
}

resource "azurerm_role_assignment" "aks_vm_contributor" {
  scope                = var.disk_encryption_set_id
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = data.azurerm_user_assigned_identity.identity.principal_id

  depends_on = [azurerm_kubernetes_cluster.aks]
}

# Data for resource group
data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}
# Data for management resource group
data "azurerm_resource_group" "mgmt_rg" {
  name = var.mgmt_resource_group_name
}

# User-assigned identity for AKS requires the Network Contributor role on the group containing the AKS NIC
# TODO: Scope this to the NIC once the setup is confirmed
resource "azurerm_role_assignment" "aks_network_contributor" {
  principal_id         = data.azurerm_user_assigned_identity.identity.principal_id
  role_definition_name = "Network Contributor"
  scope                = data.azurerm_resource_group.rg.id
}

# User-assigned identity for AKS requires the Network Contributor role on the group containing the Web subnet
# TODO: Scope this to the subnet and route table once the setup is confirmed
resource "azurerm_role_assignment" "subnet_network_contributor" {
  principal_id         = data.azurerm_user_assigned_identity.identity.principal_id
  role_definition_name = "Network Contributor"
  scope                = data.azurerm_resource_group.mgmt_rg.id
}

# TODO: Rethink once the jumpbox VM approach is finalized
# resource "azurerm_role_assignment" "rbac_cluster_admin" {
#   for_each             = toset(var.admin_group_id)
#   principal_id         = each.value
#   role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
#   scope                = azurerm_kubernetes_cluster.aks.id
# }

resource "azurerm_role_assignment" "rbac_cluster_admin" {
  principal_id         = var.jumpbox_identity_id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  scope                = azurerm_kubernetes_cluster.aks.id
}

# Role assignment for the AKS kubelet identity
resource "azurerm_role_assignment" "aks_kubelet_identity_kv_secrets_user" {
  # SC-12: RBAC for AKS to access Key Vault secrets
  principal_id         = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name = "Key Vault Secrets User"
  scope                = var.keyvault_id
  principal_type       = "ServicePrincipal"
}

# Role assignment for the AKS secrets provider identity
resource "azurerm_role_assignment" "aks_secrets_provider_identity_kv_secrets_user" {
  # SC-12: RBAC for AKS to access Key Vault secrets
  principal_id         = azurerm_kubernetes_cluster.aks.key_vault_secrets_provider[0].secret_identity[0].object_id
  role_definition_name = "Key Vault Secrets User"
  scope                = var.keyvault_id
  principal_type       = "ServicePrincipal"
}

# ## Kubelet Identity
# The kubelet identity is crucial for node-level operations, including accessing Azure resources like Key Vault. It's essential for pods that need to directly access secrets.
# 
# ## Secrets Provider Identity
# This identity is specifically used by the Azure Key Vault Provider for Secrets Store CSI Driver. It's responsible for accessing Key Vault secrets and mounting them as volumes in pods.
# 
# ## Cluster Identity
# The AKS cluster's identity is used for cluster-level operations and management tasks and does not typically require direct access to secrets.

resource "azurerm_role_assignment" "acr_pull" {
  principal_id         = data.azurerm_user_assigned_identity.identity.principal_id
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
  for_each             = toset(var.log_analytics_readers_group_id)
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

# Create private endpoint for AKS cluster
resource "azurerm_private_endpoint" "aks_private_endpoint" {
  name                = "${var.aks_cluster_name}-endpoint"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.web_subnet_id

  private_service_connection {
    name                           = "${var.aks_cluster_name}-connection"
    private_connection_resource_id = azurerm_kubernetes_cluster.aks.id
    subresource_names              = ["management"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "${var.aks_cluster_name}-dns-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.aks_dns.id]
  }

  tags = var.tags

  depends_on = [
    azurerm_kubernetes_cluster.aks,
    azurerm_private_dns_zone.aks_dns
  ]
}

# Add A record for AKS API server
resource "azurerm_private_dns_a_record" "aks_api" {
  name                = azurerm_kubernetes_cluster.aks.private_fqdn
  zone_name           = azurerm_private_dns_zone.aks_dns.name
  resource_group_name = var.resource_group_name
  ttl                 = 300
  records             = [azurerm_private_endpoint.aks_private_endpoint.private_service_connection[0].private_ip_address]

  depends_on = [
    azurerm_private_endpoint.aks_private_endpoint
  ]
}
