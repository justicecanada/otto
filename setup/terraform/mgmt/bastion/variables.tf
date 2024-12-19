variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
}

variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)
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

variable "main_vnet_id" {
  description = "The ID of the main virtual network"
  type        = string
}

