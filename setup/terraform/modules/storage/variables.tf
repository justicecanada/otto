variable "storage_name" {
  type        = string
  description = "The name of the storage account"
}

variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the storage account"
}

variable "location" {
  type        = string
  description = "The Azure region where the storage account should be created"
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

variable "cmk_name" {
  type        = string
  description = "The name of the key in the Key Vault to use for encryption"
}

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

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}
