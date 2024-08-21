variable "aks_cluster_name" {
  description = "The name of the AKS cluster"
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

variable "admin_group_object_id" {
  description = "The object ID of the admin Azure AD group"
  type        = string
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

variable "host_name_prefix" {
  description = "The prefix for the host name"
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
