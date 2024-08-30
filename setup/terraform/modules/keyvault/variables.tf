variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Key Vaults"
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

variable "admin_group_object_ids" {
  type        = list({object_id = string})
  description = "The object ID of the admin group"
  default     = []
  nullable    = false
}

variable "entra_client_secret" {
  type        = string
  description = "The client secret of the ENTRA application"
}
