variable "resource_name" {
  description = "The name of the Django database resource"
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

variable "storage_account_id" {
  description = "The ID of the storage account"
  type        = string
}

variable "keyvault_id" {
  description = "The ID of the Key Vault"
  type        = string
}

variable "wait_for_propagation" {
  description = "Flag for keyvault permission propagation"
  type        = string
}

variable "aks_ip_address" {
  description = "Outbound IP address of the AKS cluster"
  type        = string
}

variable "use_private_network" {
  type        = bool
  description = "Whether to use private networking for the Cosmos DB for PostgreSQL"
}
