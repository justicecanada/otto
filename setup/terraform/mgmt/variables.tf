variable "mgmt_resource_group_name" {
  description = "The name of the management resource group"
  type        = string
}

variable "environment" {
  description = "The environment for the resources"
  type        = string
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
}

variable "vnet_name" {
  description = "The name of the virtual network"
  type        = string
}

variable "vnet_address_space" {
  description = "The address space for the virtual network"
  type        = string
}

variable "mgmt_subnet_name" {
  description = "The name of the management subnet"
  type        = string
}

variable "mgmt_subnet_prefix" {
  description = "The address prefix for the management subnet"
  type        = string
}

variable "app_subnet_name" {
  description = "The name of the application subnet"
  type        = string
}

variable "app_subnet_prefix" {
  description = "The address prefix for the application subnet"
  type        = string
}

variable "web_subnet_name" {
  description = "The name of the web subnet"
  type        = string
}

variable "web_subnet_prefix" {
  description = "The address prefix for the web subnet"
  type        = string
}

variable "firewall_name" {
  description = "The name of the firewall"
  type        = string
}

variable "firewall_vnet_name" {
  description = "The name of the firewall virtual network"
  type        = string
}

variable "firewall_vnet_address_space" {
  description = "The address space for the firewall virtual network"
  type        = string
}

variable "firewall_subnet_prefix" {
  description = "The address prefix for the firewall subnet"
  type        = string
}

variable "bastion_name" {
  description = "The name of the Bastion host"
  type        = string
}

variable "bastion_vnet_name" {
  description = "The name of the Bastion virtual network"
  type        = string
}

variable "bastion_vnet_address_space" {
  description = "The address space for the Bastion virtual network"
  type        = string
}

variable "bastion_subnet_prefix" {
  description = "The address prefix for the Bastion subnet"
  type        = string
}

variable "bastion_subnet_name" {
  description = "The name of the Bastion subnet"
  type        = string
}

variable "mgmt_storage_account_name" {
  description = "The name of the management storage account"
  type        = string
}

variable "mgmt_storage_make_private" {
  type        = bool
  default     = true
  description = "Set to true to make the management storage account private. Change cannot be reverted once private."
}

variable "jumpbox_name" {
  description = "The name of the jumpbox VM"
  type        = string
}

variable "jumpbox_identity_name" {
  description = "The name of the jumpbox's managed identity"
  type        = string
}

variable "aks_ingress_private_ip" {
  description = "The private IP address of the AKS ingress controller"
  type        = string
}

variable "tags" {
  description = "A mapping of tags to assign to the resources"
  type        = map(string)
}