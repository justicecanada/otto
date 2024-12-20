resource "azurerm_resource_group" "mgmt_rg" {
  name     = var.mgmt_resource_group_name
  location = var.location
  tags     = merge(var.tags, { Purpose = "Management" })
}