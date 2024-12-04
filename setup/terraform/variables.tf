variable "app_name" {
  type        = string
  description = "Application name (e.g., Otto)"
}

variable "tenant_id" {
  type        = string
  description = "Azure tenant ID"
}

variable "subscription_id" {
  type        = string
  description = "Azure subscription ID"
}

variable "environment" {
  type        = string
  description = "Environment name (e.g., Sandbox, Development, Production)"
}

variable "location" {
  type        = string
  description = "Azure region for resource deployment"
}

variable "classification" {
  type        = string
  description = "Resource classification"
}

variable "cost_center" {
  type        = string
  description = "Cost center for billing"
}

variable "criticality" {
  type        = string
  description = "Resource criticality"
}

variable "owner" {
  type        = string
  description = "Owner of the resources"
}

variable "acr_id" {
  type        = string
  description = "ID of the ACR"
}

variable "log_analytics_readers_group_id" {
  type        = string
  description = "Comma-separated list of group IDs for Log Analytics readers"

}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "mgmt_resource_group_name" {
  type        = string
  description = "Name of the management resource group"
}

variable "jumpbox_identity_id" {
  type        = string
  description = "ID of the jumpber VM's user-assigned identity"
}

variable "keyvault_name" {
  type        = string
  description = "Name of the key vault"
}

variable "cognitive_services_name" {
  type        = string
  description = "Name of the cognitive services"
}

variable "openai_service_name" {
  type        = string
  description = "Name of the OpenAI service"
}

variable "aks_cluster_name" {
  type        = string
  description = "Name of the AKS cluster"
}

variable "disk_name" {
  type        = string
  description = "Name of the disk"
}

variable "storage_name" {
  type        = string
  description = "Name of the storage"
}

variable "storage_container_name" {
  description = "Name of the default container to create in the storage account"
  type        = string
  default     = "otto"
}

variable "djangodb_resource_name" {
  type        = string
  description = "Name of the Django DB resource"
}

variable "vnet_id" {
  type        = string
  description = "ID of the virtual network"
}
variable "web_subnet_id" {
  type        = string
  description = "ID of the web subnet"
}
variable "app_subnet_id" {
  type        = string
  description = "ID of the app subnet"
}

variable "gpt_35_turbo_capacity" {
  description = "GPT-3.5 Turbo quota limit"
  type        = number
}

variable "gpt_4_turbo_capacity" {
  description = "GPT-4 Turbo quota limit"
  type        = number
}

variable "gpt_4o_capacity" {
  description = "GPT-4o quota limit"
  type        = number
}

variable "gpt_4o_mini_capacity" {
  description = "GPT-4o Mini quota limit"
  type        = number
}

variable "text_embedding_3_large_capacity" {
  description = "Text Embedding 3 Large quota limit"
  type        = number
}

variable "admin_email" {
  description = "Admin email address"
  type        = string
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the infrastructure"
}

# variable "velero_identity_name" {
#   description = "Name of the Velero managed identity"
#   type        = string
#   default     = "velero"
# }

variable "approved_cpu_quota" {
  type        = number
  description = "The approved CPU quota for the AKS cluster"
  default     = 10
}

variable "vm_size" {
  type        = string
  description = "The size of VM to use for the AKS cluster"
  default     = "Standard_D4s_v3" # General-purpose VM with 4 CPUs, 16 GiB RAM, and 32 GiB temporary storage
}

variable "vm_cpu_count" {
  type        = number
  description = "The number of CPUs in the selected VM size"
  default     = 4 # Standard_D4s_v3 has 4 CPUs
}

