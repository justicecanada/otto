variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Key Vaults"
}

variable "environment" {
  type        = string
  description = "The environment in which the Key Vaults are being created"
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

variable "mgmt_subnet_id" {
  type        = string
  description = "The ID of the management subnet"
}

variable "keyvault_private_dns_zone_id" {
  type        = string
  description = "The ID of the private DNS zone for the Key Vaults"
}

variable "vnet_id" {
  type        = string
  description = "The ID of the VNet to which the Key Vaults should be linked"
}

variable "app_subnet_id" {
  description = "The ID of the app subnet"
  type        = string
}


