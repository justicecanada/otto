
variable "vnet_name" {
  type        = string
  description = "Name of the virtual network"
}
variable "vnet_ip_range" {
  type        = string
  description = "IP range of the virtual network"
}
variable "web_subnet_name" {
  type        = string
  description = "Name of the web subnet"
}
variable "web_subnet_ip_range" {
  type        = string
  description = "IP range of the web subnet"
}
variable "app_subnet_name" {
  type        = string
  description = "Name of the app subnet"
}
variable "app_subnet_ip_range" {
  type        = string
  description = "IP range of the app subnet"
}
variable "location" {
  type        = string
  description = "Azure region for resource deployment"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "tags" {
  type        = map(string)
  description = "A mapping of tags to assign to the resource"
}
