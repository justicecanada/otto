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
}
