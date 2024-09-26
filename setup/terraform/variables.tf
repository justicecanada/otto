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

variable "admin_group_name" {
  type        = string
  description = "Comma-separated list of group names for admin users on the Key Vault and AKS cluster"
}

variable "acr_publishers_group_name" {
  type        = string
  description = "Comma-separated list of group names for ACR publishers"
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

variable "storage_container_name" {
  description = "Name of the default container to create in the storage account"
  type        = string
  default     = "otto"
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

variable "gpt_35_turbo_capacity" {
  description = "GPT-3.5 Turbo quota limit"
  type        = number
}

variable "gpt_4_turbo_capacity" {
  description = "GPT-4 Turbo quota limit"
  type        = number
}

variable "gpt_4o_capacity" {
  description = "GPT-4o quota limit"
  type        = number
}

variable "gpt_4o_mini_capacity" {
  description = "GPT-4o Mini quota limit"
  type        = number
}

variable "text_embedding_3_large_capacity" {
  description = "Text Embedding 3 Large quota limit"
  type        = number
}
