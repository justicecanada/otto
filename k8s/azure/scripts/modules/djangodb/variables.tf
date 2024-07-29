variable "db_name" {
  description = "The name of the PostgreSQL database"
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

variable "tags" {
  description = "A mapping of tags to assign to the resource"
  type        = map(string)
  default     = {}
}

variable "keyvault_id" {
  description = "The ID of the Key Vault"
  type        = string
}
