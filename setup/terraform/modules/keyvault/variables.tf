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

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the Key Vaults"
}

variable "app_subnet_id" {
  description = "The ID of the app subnet"
  type        = string
}

variable "web_subnet_id" {
  description = "The ID of the web subnet"
  type        = string
}

variable "db_subnet_id" {
  description = "The ID of the database subnet"
  type        = string
}

variable "corporate_public_ip" {
  description = "The public IP address of the corporate network"
  type        = string
}
