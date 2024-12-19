locals {
  # CM-8: Common tags are defined for all resources
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
