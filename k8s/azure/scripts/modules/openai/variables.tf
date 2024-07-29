variable "name" {
  type        = string
  description = "The name of the Cognitive Services OpenAI service"
}

variable "location" {
  type        = string
  description = "The Azure region where the Cognitive Services OpenAI account should be created"
}

variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the Cognitive Services OpenAI account"
}

variable "tags" {
  type        = map(string)
  description = "A map of tags to apply to the Cognitive Services OpenAI account"
}

variable "keyvault_id" {
  type        = string
  description = "The ID of the Key Vault where the Cognitive Services OpenAI key will be stored"
}
