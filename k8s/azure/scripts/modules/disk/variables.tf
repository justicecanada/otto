variable "disk_name" {
  description = "The name of the disk"
  type        = string
}

variable "location" {
  description = "The Azure region for resource deployment"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "ssd_disk_size" {
  description = "The size of the SSD disk in GB"
  type        = number
  default     = 64
}

variable "hdd_disk_size" {
  description = "The size of the HDD disk in GB"
  type        = number
  default     = 500
}

variable "tags" {
  description = "A mapping of tags to assign to the resource"
  type        = map(string)
  default     = {}
}

variable "aks_cluster_id" {
  description = "The ID of the AKS cluster"
  type        = string
}
