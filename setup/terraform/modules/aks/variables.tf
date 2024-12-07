variable "aks_cluster_name" {
  description = "The name of the AKS cluster"
  type        = string
}

variable "location" {
  description = "The Azure region for resource deployment"
  type        = string
}

variable "jumpbox_identity_id" {
  description = "The ID of the jumpbox's user-assigned managed identity"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "mgmt_resource_group_name" {
  description = "The name of the management resource group"
  type        = string
}

variable "log_analytics_readers_group_id" {
  description = "The list of objects IDs of the Log Analytics readers Azure AD group"
  type        = list(string)
}

variable "keyvault_id" {
  description = "The ID of the keyvault"
  type        = string
}

variable "disk_encryption_set_id" {
  description = "The ID of the disk encryption set"
  type        = string
}

variable "acr_id" {
  description = "The ID of the Azure Container Registry"
  type        = string
}

variable "tags" {
  description = "A mapping of tags to assign to the resource"
  type        = map(string)
}

variable "storage_account_id" {
  description = "The ID of the storage account"
  type        = string
}

variable "admin_email" {
  description = "The email address of the admin user"
  type        = string
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the AKS cluster"
}

variable "service_cidr" {
  type        = string
  description = "The CIDR block for the AKS service"
}

variable "dns_service_ip" {
  type        = string
  description = "The IP address for the AKS DNS service"
}

variable "vnet_id" {
  type        = string
  description = "The ID of the VNet for the AKS cluster"
}

variable "web_subnet_id" {
  type        = string
  description = "The ID of the subnet for the web app"
}

variable "approved_cpu_quota" {
  type        = number
  description = "The approved CPU quota for the AKS cluster"
}

variable "vm_size" {
  type        = string
  description = "The size of VM to use for the AKS cluster"
}

variable "vm_cpu_count" {
  type        = number
  description = "The number of CPUs in the selected VM size"
}
