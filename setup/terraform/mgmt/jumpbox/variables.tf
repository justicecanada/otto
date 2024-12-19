variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure region where the resource group should be created"
  type        = string
}

variable "tags" {
  description = "A mapping of tags to assign to the resource group"
  type        = map(string)
  default     = {}
}

variable "jumpbox_name" {
  description = "The name of the jumpbox VM"
  type        = string
}

variable "jumpbox_identity_id" {
  description = "The identity ID of the jumpbox VM"
  type        = string
}

variable "subnet_id" {
  description = "The ID of the subnet to place the jumpbox VM in"
  type        = string
}

variable "vm_size" {
  description = "The size of the jumpbox VM"
  type        = string
  default     = "Standard_B2s"
}