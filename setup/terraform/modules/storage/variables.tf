variable "storage_name" {
  type        = string
  description = "The name of the storage account"
}

variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the storage account"
}

variable "mgmt_resource_group_name" {
  type        = string
  description = "The name of the management resource group"
}

variable "location" {
  type        = string
  description = "The Azure region where the storage account should be created"
}

variable "identity_id" {
  type        = string
  description = "The ID of the user-assigned managed identity to assign to the storage account"
}

variable "jumpbox_identity_id" {
  type        = string
  description = "The ID of the jumpbox's user-assigned managed identity to manage the storage account"
}

variable "tags" {
  type        = map(string)
  description = "A mapping of tags to assign to the resource"
  default     = {}
}

variable "keyvault_id" {
  type        = string
  description = "The ID of the Key Vault where the storage key will be stored"
}

# TODO: Uncomment if we want CMK managed by Terraform again.
# variable "cmk_name" {
#   type        = string
#   description = "The name of the key in the Key Vault to use for encryption"
# }

variable "wait_for_propagation" {
  description = "Flag for keyvault permission propagation"
  type        = string
}

variable "storage_container_name" {
  description = "The name of the default container to create in the storage account"
  type        = string
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the storage account"
}

variable "vnet_id" {
  type        = string
  description = "The ID of the VNet to which the storage account should be linked"
}

variable "app_subnet_id" {
  description = "The ID of the app subnet"
  type        = string
}

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}
