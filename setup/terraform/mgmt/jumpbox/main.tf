resource "azurerm_network_interface" "jumpbox_nic" {
  name                = "${var.jumpbox_name}-nic"
  location            = var.location
  resource_group_name = var.resource_group_name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Dynamic"
  }

  tags = var.tags
}

resource "tls_private_key" "jumpbox_ssh" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "azurerm_linux_virtual_machine" "jumpbox" {
  name                = var.jumpbox_name
  resource_group_name = var.resource_group_name
  location            = var.location
  size                = var.vm_size
  admin_username      = "azureuser"
  network_interface_ids = [
    azurerm_network_interface.jumpbox_nic.id,
  ]

  admin_ssh_key {
    username   = "azureuser"
    public_key = tls_private_key.jumpbox_ssh.public_key_openssh
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "UbuntuServer"
    sku       = "22.04-LTS"
    version   = "latest"
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [var.jumpbox_identity_id]
  }

  tags = var.tags
}
