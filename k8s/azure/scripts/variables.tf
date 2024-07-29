# variables.tf

variable "environment" {
  type        = string
  description = "Environment name (e.g., Sandbox, Development, Production)"
}

variable "intended_use" {
  type        = string
  description = "Intended use of the resources (e.g., dev, uat, pilot, prod)"
  default     = "Development and testing"
}

variable "subscription" {
  type        = string
  description = "Azure subscription name"
}

variable "app_name" {
  type        = string
  description = "Application name (e.g., Otto)"
}

variable "location" {
  type        = string
  description = "Azure region for resource deployment"
  default     = "canadacentral"
}

variable "classification" {
  type        = string
  description = "Resource classification"
  default     = "Unclassified"
}

variable "cost_center" {
  type        = string
  description = "Cost center for billing"
  default     = "Business Analytics Center (CC 12031)"
}

variable "criticality" {
  type        = string
  description = "Resource criticality"
  default     = "NonEssential"
}

variable "owner" {
  type        = string
  description = "Owner of the resources"
  default     = "Business Analytics Centre"
}

variable "environment_tag" {
  type        = string
  description = "Environment tag for resources"
  default     = "Sandbox"
}

variable "group_name" {
  type        = string
  description = "Group name for access control"
  default     = "JUS.AZ.S BAC TECH S.AZ.JUS"
}

variable "disk_storage_class" {
  type        = string
  description = "Storage class for the disk"
  default     = "StandardSSD_LRS"
}
