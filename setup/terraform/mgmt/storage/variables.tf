variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "environment" {
  description = "The environment for the resources"
  type        = string
}

variable "mgmt_storage_account_name" {
  description = "The name of the storage account"
  type        = string
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
}

variable "mgmt_subnet_id" {
  description = "The ID of the management subnet"
  type        = string
}

variable "vnet_id" {
  description = "The ID of the virtual network"
  type        = string
}

variable "tags" {
  description = "A mapping of tags to assign to the resources"
  type        = map(string)
}

variable "mgmt_storage_make_private" {
  type        = bool
  default     = true
  description = "Set to true to make the management storage account private. Change cannot be reverted once private."
}