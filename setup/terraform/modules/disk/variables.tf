variable "disk_name" {
  description = "The name of the disk"
  type        = string
}

variable "location" {
  description = "The Azure region for resource deployment"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "ssd_disk_size" {
  description = "The size of the SSD disk in GB"
  type        = number
  default     = 64
}

variable "hdd_disk_size" {
  description = "The size of the HDD disk in GB"
  type        = number
  default     = 500
}

variable "tags" {
  description = "A mapping of tags to assign to the resource"
  type        = map(string)
  default     = {}
}

variable "aks_cluster_id" {
  description = "The ID of the AKS cluster"
  type        = string
}

variable "keyvault_id" {
  description = "The ID of the Key Vault"
  type        = string
}

variable "cmk_id" {
  description = "The ID of the Key in the Key Vault to use for encryption"
  type        = string
}

variable "wait_for_propagation" {
  description = "Flag for keyvault permission propagation"
  type        = string
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the managed disks"
}

variable "app_subnet_id" {
  description = "The ID of the app subnet"
  type        = string
}

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}

variable "identity_id" {
  description = "The ID of the user-assigned managed identity to assign to the disk"
  type        = string
}


