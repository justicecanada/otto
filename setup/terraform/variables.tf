variable "app_name" {
  type        = string
  description = "Application name (e.g., Otto)"
}

variable "environment" {
  type        = string
  description = "Environment name (e.g., Sandbox, Development, Production)"
}

variable "location" {
  type        = string
  description = "Azure region for resource deployment"
}

variable "classification" {
  type        = string
  description = "Resource classification"
}

variable "cost_center" {
  type        = string
  description = "Cost center for billing"
}

variable "criticality" {
  type        = string
  description = "Resource criticality"
}

variable "owner" {
  type        = string
  description = "Owner of the resources"
}

variable "group_name" {
  type        = string
  description = "Group name for access control"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "keyvault_name" {
  type        = string
  description = "Name of the key vault"
}

variable "cognitive_services_name" {
  type        = string
  description = "Name of the cognitive services"
}

variable "openai_service_name" {
  type        = string
  description = "Name of the OpenAI service"
}

variable "aks_cluster_name" {
  type        = string
  description = "Name of the AKS cluster"
}

variable "disk_name" {
  type        = string
  description = "Name of the disk"
}

variable "storage_name" {
  type        = string
  description = "Name of the storage"
}

variable "acr_name" {
  type        = string
  description = "Name of the ACR"
}

variable "djangodb_resource_name" {
  type        = string
  description = "Name of the Django DB resource"
}

variable "entra_client_secret" {
  description = "Entra client secret"
  type        = string
  sensitive   = true
}

variable "host_name_prefix" {
  description = "Prefix for the host name"
  type        = string
}
