variable "resource_group_name" {
  description = "The name of the management resource group"
  type        = string
}

variable "location" {
  description = "The Azure region where the resource groups should be created"
  type        = string
}

variable "tags" {
  description = "A mapping of tags to assign to the resource groups"
  type        = map(string)
  default     = {}
}

variable "identity_name" {
  description = "The name of the managed identity"
  type        = string
}
