output "jumpbox_id" {
  value       = azurerm_linux_virtual_machine.jumpbox.id
  description = "The ID of the jumpbox VM"
}

output "jumpbox_private_ip" {
  value       = azurerm_network_interface.jumpbox_nic.private_ip_address
  description = "The private IP address of the jumpbox VM"
}

output "jumpbox_private_key" {
  value     = tls_private_key.jumpbox_ssh.private_key_pem
  sensitive = true
}
