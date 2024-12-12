variable "acr_name" {
  description = "The name of the ACR"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure region for resource deployment"
  type        = string
}

variable "acr_sku" {
  description = "The SKU (pricing tier) of the ACR"
  type        = string
  default     = "Basic"
}

variable "tags" {
  description = "A mapping of tags to assign to the resource"
  type        = map(string)
  default     = {}
}

variable "jumpbox_identity_id" {
    description = "The ID of the managed identity for the jumpbox"
    type        = string
}

variable "keyvault_id" {
  description = "The ID of the Key Vault"
  type        = string
}
