variable "location" {
  description = "Azure region where resources will be created"
  type        = string
}

variable "resource_group_name" {
  description = "Name of the resource group"
  type        = string
}

variable "tags" {
  description = "Tags to be applied to resources"
  type        = map(string)
}

variable "firewall_name" {
  description = "Name of the Azure Firewall"
  type        = string
}

variable "firewall_vnet_name" {
  description = "Name of the Virtual Network for the Azure Firewall"
  type        = string
}

variable "firewall_vnet_address_space" {
  description = "Address space for the Firewall VNet"
  type        = string
}

variable "firewall_subnet_prefix" {
  description = "Address prefix for the AzureFirewallSubnet"
  type        = string
}

variable "aks_ingress_private_ip" {
  description = "Private IP of the AKS ingress"
  type        = string
}

variable "main_vnet_id" {
  description = "The ID of the main virtual network"
  type        = string
}
