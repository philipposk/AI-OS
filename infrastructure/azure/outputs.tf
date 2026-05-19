output "public_ip" {
  value = azurerm_public_ip.main.ip_address
}

output "ssh_command" {
  value = "ssh ubuntu@${azurerm_public_ip.main.ip_address}"
}

output "tunnel_command" {
  value = "ssh -L 8501:127.0.0.1:8501 ubuntu@${azurerm_public_ip.main.ip_address}"
}
