module "resource_group" {
  source                    = "./resource_group"
  mgmt_resource_group_name  = var.mgmt_resource_group_name
  location                  = var.location
  tags                      = var.tags
}

module "identity" {
  source                = "./identity"
  resource_group_name   = module.resource_group.name
  location              = var.location
  identity_name         = var.jumpbox_identity_name
  tags                  = var.tags
}

module "main_vnet" {
  source                    = "./main_vnet"
  environment               = var.environment
  resource_group_name       = module.resource_group.name
  location                  = var.location
  vnet_name                 = var.vnet_name
  vnet_address_space        = var.vnet_address_space
  mgmt_subnet_name          = var.mgmt_subnet_name
  mgmt_subnet_prefix        = var.mgmt_subnet_prefix
  app_subnet_name           = var.app_subnet_name
  app_subnet_prefix         = var.app_subnet_prefix
  web_subnet_name           = var.web_subnet_name
  web_subnet_prefix         = var.web_subnet_prefix
  tags                      = var.tags
}

module "firewall" {
  source                      = "./firewall"
  resource_group_name         = module.resource_group.name
  location                    = var.location
  firewall_name               = var.firewall_name
  firewall_vnet_name          = var.firewall_vnet_name
  firewall_vnet_address_space = var.firewall_vnet_address_space
  firewall_subnet_prefix      = var.firewall_subnet_prefix
  aks_ingress_private_ip      = var.aks_ingress_private_ip
  main_vnet_id                = module.main_vnet.vnet_id
  tags                        = var.tags
}

module "bastion" {
  source                      = "./bastion"
  resource_group_name         = module.resource_group.name
  location                    = var.location
  bastion_name                = var.bastion_name
  bastion_vnet_name           = var.bastion_vnet_name
  bastion_vnet_address_space  = var.bastion_vnet_address_space
  bastion_subnet_prefix       = var.bastion_subnet_prefix
  main_vnet_id                = module.main_vnet.vnet_id
  tags                        = var.tags
}

module "storage" {
  source                     = "./storage"
  resource_group_name        = module.resource_group.name
  environment                = var.environment
  location                   = var.location
  mgmt_storage_account_name  = var.mgmt_storage_account_name
  mgmt_storage_make_private  = var.mgmt_storage_make_private
  mgmt_subnet_id             = module.main_vnet.snet_mgmt_id
  vnet_id                    = module.main_vnet.vnet_id
  tags                       = var.tags
}

module "jumpbox" {
  source                = "./jumpbox"
  resource_group_name   = module.resource_group.name
  location              = var.location
  jumpbox_name          = var.jumpbox_name
  jumpbox_identity_id   = module.identity.identity_id
  subnet_id             = module.main_vnet.snet_mgmt_id
  tags                  = var.tags
}
