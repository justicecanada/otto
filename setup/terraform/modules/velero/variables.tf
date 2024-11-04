variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Velero storage account"
}

variable "location" {
  type        = string
  description = "The Azure region where the Velero storage account should be created"
}

variable "oidc_issuer_url" {
  type        = string
  description = "The OIDC issuer URL of the AKS cluster"
}
