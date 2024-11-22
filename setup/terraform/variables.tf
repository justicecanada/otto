variable "app_name" {
  type        = string
  description = "Application name (e.g., Otto)"
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

variable "admin_group_name" {
  type        = string
  description = "Comma-separated list of group names for admin users on the Key Vault and AKS cluster"
}

variable "acr_publishers_group_name" {
  type        = string
  description = "Comma-separated list of group names for ACR publishers"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
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

variable "acr_name" {
  type        = string
  description = "Name of the ACR"
}

variable "djangodb_resource_name" {
  type        = string
  description = "Name of the Django DB resource"
}

variable "vnet_name" {
  type        = string
  description = "Name of the virtual network"
}
variable "vnet_ip_range" {
  type        = string
  description = "IP range of the virtual network"
}
variable "web_subnet_name" {
  type        = string
  description = "Name of the web subnet"
}
variable "web_subnet_ip_range" {
  type        = string
  description = "IP range of the web subnet"
}
variable "app_subnet_name" {
  type        = string
  description = "Name of the app subnet"
}
variable "app_subnet_ip_range" {
  type        = string
  description = "IP range of the app subnet"
}
variable "db_subnet_name" {
  type        = string
  description = "Name of the database subnet"
}
variable "db_subnet_ip_range" {
  type        = string
  description = "IP range of the database subnet"
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

variable "backup_container_name" {
  description = "Name of the backup container"
  type        = string
  default     = "backups"
}

variable "velero_identity_name" {
  description = "Name of the Velero managed identity"
  type        = string
  default     = "velero"
}

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

variable "corporate_public_ip" {
  description = "The public IP address of the corporate network"
  type        = string
  validation {
    condition     = can(regex("^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$", var.corporate_public_ip))
    error_message = "The corporate_public_ip value must be a valid IPv4 address."
  }
}
