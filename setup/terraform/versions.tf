terraform {
  required_version = ">= 1.0"
  required_providers {
    azapi = {
      source  = "azure/azapi"
      version = "~> 1.5"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  # Specifies how Terraform should authenticate with Azure when creating, updating, or deleting resources
  # Is used when Terraform is making API calls to Azure to manage resources
  # Relies on the ARM environment variables
  features {}
}

provider "azapi" {
}
