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
  type        = list(string)
  description = "List of object IDs of the admin Azure AD groups"
}

variable "entra_client_secret" {
  type        = string
  description = "The client secret of the ENTRA application"
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the Key Vaults"
}

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}
