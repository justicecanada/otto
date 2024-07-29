locals {
  resource_group_name     = "${var.app_name}${upper(var.intended_use)}Rg"
  keyvault_name           = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-kv"
  admin_keyvault_name     = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-admkv"
  cognitive_services_name = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-cs"
  openai_service_name     = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-openai"
  aks_cluster_name        = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-aks"
  disk_name               = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-disk"
  storage_name            = "jus${lower(var.intended_use)}${lower(var.app_name)}storage"
  acr_name                = "jus${lower(var.intended_use)}${lower(var.app_name)}acr"
  djangodb_name           = "jus-${lower(var.intended_use)}-${lower(var.app_name)}-db"
  
  # Common tags
  common_tags = {
    Application    = var.app_name
    Classification = var.classification
    CostCenter     = var.cost_center
    Criticality    = var.criticality
    Environment    = var.environment
    Location       = var.location
    Owner          = var.owner
  }
}
