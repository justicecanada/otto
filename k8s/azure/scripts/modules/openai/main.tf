# https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/quota?tabs=terraform

terraform {
  required_providers {
    azapi = {
      source  = "azure/azapi"
      version = "~> 1.5.0"  # Use the latest version available
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"  # Use an appropriate version
    }
  }
}


# Azure Cognitive Account for OpenAI
resource "azurerm_cognitive_account" "openai" {
  name                = "jus-test-ottoa7-openai-tmp" # var.name
  location            = "canadaeast" # The models are not available in the Canada Central region
  resource_group_name = var.resource_group_name
  kind                = "OpenAI"
  sku_name            = "S0"

  lifecycle {
    ignore_changes = all
  }

  tags                = var.tags
  depends_on          = [var.keyvault_id]
}


resource "azurerm_key_vault_secret" "openai_key" {
  name         = "OPENAI-SERVICE-KEY"
  value        = azurerm_cognitive_account.openai.primary_access_key
  key_vault_id = var.keyvault_id
}


# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "time_sleep" "wait_30_seconds" {
  create_duration = "30s"
}


# GPT-3.5 Turbo deployment
resource "azapi_resource" "gpt-35-turbo-deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name      = "gpt-35-turbo-deployment"
  parent_id = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "Standard"
      capacity = 120 # TODO: Make this a variable. Shared across instances in the subscription.
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-35-turbo"
        version = "0613"
      }
      raiPolicyName = "Microsoft.Default"
    }
  })

  depends_on = [time_sleep.wait_30_seconds]
}


# GPT-3.5 Turbo 16k deployment
resource "azapi_resource" "gpt-35-turbo-16k-deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name      = "gpt-35-turbo-16k-deployment"
  parent_id = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "Standard"
      capacity = 90 # TODO: Make this a variable. Shared across instances in the subscription.
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-35-turbo-16k"
        version = "0613"
      }
      raiPolicyName = "Microsoft.Default"
    }
  })

  # Cannot add deployments concurrently
  depends_on = [time_sleep.wait_30_seconds]
}


# GPT-4 deployment
resource "azapi_resource" "gpt-4-deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name      = "gpt-4-deployment"
  parent_id = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
        name = "Standard",             
        capacity = 10 # TODO: Make this a variable. Shared across instances in the subscription.
    },
    properties = {
        model = {
            format = "OpenAI",
            name = "gpt-4",
            version = "1106-Preview"
        }
        raiPolicyName = "Microsoft.Default"
    }
  })

  # Cannot add deployments concurrently
  depends_on = [time_sleep.wait_30_seconds]
}


# Text embedding large deployment
resource "azapi_resource" "text-embedding-3-large-deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name      = "text-embedding-3-large-deployment"
  parent_id = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
        name = "Standard",             
        capacity = 100 # TODO: Make this a variable. Shared across instances in the subscription.
    },
    properties = {
        model = {
            format = "OpenAI",
            name = "text-embedding-3-large",
            version = "1"
        }
        raiPolicyName = "Microsoft.Default"
    }
  })

  # Cannot add deployments concurrently
  depends_on = [time_sleep.wait_30_seconds]
}
