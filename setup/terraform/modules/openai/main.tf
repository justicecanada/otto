terraform {
  required_providers {
    azapi = {
      source  = "azure/azapi"
      version = "~> 1.5.0" # Use the latest version available
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0" # Use an appropriate version
    }
  }
}

# Azure Cognitive Account for OpenAI
resource "azurerm_cognitive_account" "openai" {
  name                = var.name
  location            = "canadaeast" # The models are not available in the Canada Central region
  resource_group_name = var.resource_group_name
  kind                = "OpenAI"
  sku_name            = "S0"

  lifecycle {
    ignore_changes = all
  }

  tags       = var.tags
  depends_on = [var.keyvault_id]
}

resource "azurerm_key_vault_secret" "openai_key" {
  name         = "OPENAI-SERVICE-KEY"
  value        = azurerm_cognitive_account.openai.primary_access_key
  key_vault_id = var.keyvault_id

  depends_on = [var.wait_for_propagation]
}

# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "null_resource" "wait_for_openai_resource" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azurerm_key_vault_secret.openai_key, azurerm_cognitive_account.openai]
}

resource "azapi_resource" "rai_policy" {
  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@2024-04-01-preview"
  name                      = "Unfiltered"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    properties = {
      mode = "Default"
      basePolicyName : "Microsoft.Default",
      contentFilters : [
        {
          name : "hate",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Prompt"
        },
        {
          name : "sexual",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Prompt"
        },
        {
          name : "selfharm",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Prompt"
        },
        {
          name : "violence",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Prompt"
        },
        {
          name : "hate",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Completion"
        },
        {
          name : "sexual",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Completion"
        },
        {
          name : "selfharm",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Completion"
        },
        {
          name : "violence",
          allowedContentLevel : "Medium",
          blocking : false,
          enabled : false,
          source : "Completion"
        }
      ]
    }
  })

  depends_on = [null_resource.wait_for_openai_resource]
}

# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "null_resource" "wait_for_rai_policy" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azapi_resource.rai_policy]
}


# GPT-3.5 Turbo deployment
resource "azapi_resource" "gpt-35-turbo-deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name                      = "gpt-35"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "Standard"
      capacity = var.gpt_35_turbo_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-35-turbo"
        version = "0125"
      }
      raiPolicyName = "Unfiltered"
    }
  })

  depends_on = [null_resource.wait_for_rai_policy]
}

# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "null_resource" "wait_for_openai_deployment_1" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azapi_resource.gpt-35-turbo-deployment, null_resource.wait_for_openai_resource]
}

# GPT-4 deployment
resource "azapi_resource" "gpt-4-deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name                      = "gpt-4"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "Standard",
      capacity = var.gpt_4_turbo_capacity
    },
    properties = {
      model = {
        format  = "OpenAI",
        name    = "gpt-4",
        version = "1106-Preview"
      }
      raiPolicyName = "Unfiltered"
    }
  })

  depends_on = [null_resource.wait_for_openai_deployment_1]
}

# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "null_resource" "wait_for_openai_deployment_2" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azapi_resource.gpt-4-deployment]
}

# GPT-4o deployment
resource "azapi_resource" "gpt-4o-deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name                      = "gpt-4o"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "GlobalStandard",
      capacity = var.gpt_4o_capacity
    },
    properties = {
      model = {
        format  = "OpenAI",
        name    = "gpt-4o",
        version = "2024-05-13"
      }
      raiPolicyName = "Unfiltered"
    }
  })

  depends_on = [null_resource.wait_for_openai_deployment_2]
}

# A delay is required to avoid a 409 conflict error when adding deployments concurrently
resource "null_resource" "wait_for_openai_deployment_3" {
  provisioner "local-exec" {
    command = "sleep 60"
  }
  depends_on = [azapi_resource.gpt-4o-deployment]
}

# Text embedding large deployment
resource "azapi_resource" "text-embedding-3-large-deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name                      = "text-embedding-3-large"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false

  body = jsonencode({
    sku = {
      name     = "Standard",
      capacity = var.text_embedding_3_large_capacity
    },
    properties = {
      model = {
        format  = "OpenAI",
        name    = "text-embedding-3-large",
        version = "1"
      }
      raiPolicyName = "Unfiltered"
    }
  })

  depends_on = [null_resource.wait_for_openai_deployment_3]
}
