locals {
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

  admin_group_names = toset(var.admin_group_names)
  acr_publishers_group_names = toset(var.acr_publishers_group_names)
}
