variable "environment" {
  description = "The environment for the resources"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the management resource group"
  type        = string
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
}

variable "vnet_name" {
  description = "The name of the main virtual network"
  type        = string
}

variable "vnet_address_space" {
  description = "The address space for the main virtual network"
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

variable "tags" {
  description = "A mapping of tags to assign to the resources"
  type        = map(string)
}
