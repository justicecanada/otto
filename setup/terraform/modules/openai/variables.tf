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

variable "wait_for_propagation" {
  description = "Flag for keyvault permission propagation"
  type        = string
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

variable "text_embedding_3_large_capacity" {
  description = "Text Embedding 3 Large quota limit"
  type        = number
}
