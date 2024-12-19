resource "azurerm_virtual_network" "firewall_vnet" {
  name                = var.firewall_vnet_name
  address_space       = [var.firewall_vnet_address_space]
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_servers         = ["168.63.129.16"]
  tags                = var.tags
}

resource "azurerm_subnet" "fw_subnet" {
  name                 = "AzureFirewallSubnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.firewall_vnet.name
  address_prefixes     = [var.firewall_subnet_prefix]
}

resource "azurerm_public_ip" "fw_pip" {
  name                = "pip-${var.firewall_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_firewall" "main" {
  name                = "fw-${var.firewall_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"

  ip_configuration {
    name                 = "fw-ipconfig"
    subnet_id            = azurerm_subnet.fw_subnet.id
    public_ip_address_id = azurerm_public_ip.fw_pip.id
  }
}

resource "azurerm_firewall_network_rule_collection" "inbound_rules" {
  name                = "nrc-inbound-rules"
  azure_firewall_name = azurerm_firewall.main.name
  resource_group_name = var.resource_group_name
  priority            = 100
  action              = "Allow"

  rule {
    name                  = "r-allow-inbound-https"
    source_addresses      = ["199.212.215.11"] # Justice Canada IP
    destination_ports     = ["443"]
    destination_addresses = [var.aks_ingress_private_ip]
    protocols             = ["TCP"]
  }
}

resource "azurerm_firewall_application_rule_collection" "outbound_rules" {
  name                = "arc-outbound-rules"
  azure_firewall_name = azurerm_firewall.main.name
  resource_group_name = var.resource_group_name
  priority            = 200
  action              = "Allow"

  rule {
    name = "r-allow-mcr-and-azure-services"
    source_addresses = [var.firewall_vnet_address_space]
    target_fqdns = [
      "mcr.microsoft.com", "*.data.mcr.microsoft.com", "mcr-0001.mcr-msedge.net",
      "management.azure.com", "login.microsoftonline.com", "packages.microsoft.com",
      "acs-mirror.azureedge.net", "*.api.cognitive.microsoft.com", "*.openai.azure.com"
    ]
    protocol {
      port = "443"
      type = "Https"
    }
  }

  rule {
    name = "r-allow-ubuntu-updates"
    source_addresses = [var.firewall_vnet_address_space]
    target_fqdns = [
      "security.ubuntu.com", "azure.archive.ubuntu.com", "changelogs.ubuntu.com"
    ]
    protocol {
      port = "80"
      type = "Http"
    }
  }

  rule {
    name = "r-allow-ubuntu-snapshot"
    source_addresses = [var.firewall_vnet_address_space]
    target_fqdns = ["snapshot.ubuntu.com"]
    protocol {
      port = "443"
      type = "Https"
    }
  }

  rule {
    name = "r-allow-whitelisted-sites"
    source_addresses = [var.firewall_vnet_address_space]
    target_fqdns = [
      "*.canada.ca", "canada.ca", "*.gc.ca", "canlii.org", "*.canlii.org",
      "wikipedia.org", "scc-csc.ca", "*.scc-csc.ca", "parl.ca", "*.parl.ca"
    ]
    protocol {
      port = "443"
      type = "Https"
    }
  }

  rule {
    name = "r-allow-letsencrypt"
    source_addresses = [var.firewall_vnet_address_space]
    target_fqdns = ["acme-v02.api.letsencrypt.org"]
    protocol {
      port = "443"
      type = "Https"
    }
  }
}

resource "azurerm_firewall_network_rule_collection" "ntp_rule" {
  name                = "nrc-ntp-rule"
  azure_firewall_name = azurerm_firewall.main.name
  resource_group_name = var.resource_group_name
  priority            = 300
  action              = "Allow"

  rule {
    name                  = "r-allow-ntp"
    source_addresses      = [var.firewall_vnet_address_space]
    destination_ports     = ["123"]
    destination_addresses = ["*"]
    protocols             = ["UDP"]
  }
}
