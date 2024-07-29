variable "name" {
  type        = string
  description = "The name of the Cognitive Services account"
}

variable "location" {
  type        = string
  description = "The Azure region where the Cognitive Services account should be created"
}

variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Cognitive Services account"
}

variable "keyvault_id" {
  type        = string
  description = "The ID of the Key Vault where the Cognitive Services key will be stored"
}

variable "tags" {
  type        = map(string)
  description = "A mapping of tags to assign to the resource"
  default     = {}
}
