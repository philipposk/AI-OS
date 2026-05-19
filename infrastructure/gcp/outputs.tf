output "public_ip" {
  value = google_compute_instance.ai_worker.network_interface[0].access_config[0].nat_ip
}

output "ssh_command" {
  value = "ssh ubuntu@${google_compute_instance.ai_worker.network_interface[0].access_config[0].nat_ip}"
}

output "tunnel_command" {
  value = "ssh -L 8501:127.0.0.1:8501 ubuntu@${google_compute_instance.ai_worker.network_interface[0].access_config[0].nat_ip}"
}
