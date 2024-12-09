variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Key Vaults"
}

variable "mgmt_resource_group_name" {
  type        = string
  description = "The name of the management resource group containing the private DNS zone"
}

variable "location" {
  type        = string
  description = "The Azure region where the Key Vaults should be created"
}

variable "keyvault_name" {
  type        = string
  description = "The name of the main Key Vault"
}

variable "tags" {
  type        = map(string)
  description = "A mapping of tags to assign to the Key Vaults"
  default     = {}
}

variable "jumpbox_identity_id" {
  type        = string
  description = "The ID of the jumpbox's user-assigned managed identity to manage the Key Vaults"
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the Key Vaults"
}

variable "vnet_id" {
  type        = string
  description = "The ID of the VNet to which the Key Vaults should be linked"
}

variable "app_subnet_id" {
  description = "The ID of the app subnet"
  type        = string
}

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}


