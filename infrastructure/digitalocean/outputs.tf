output "public_ip" {
  value = digitalocean_droplet.ai_worker.ipv4_address
}

output "ssh_command" {
  value = "ssh root@${digitalocean_droplet.ai_worker.ipv4_address}"
}

output "tunnel_command" {
  value = "ssh -L 8501:127.0.0.1:8501 root@${digitalocean_droplet.ai_worker.ipv4_address}"
}
