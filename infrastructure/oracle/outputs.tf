output "public_ip" {
  description = "Public IPv4 of the AI Company VM."
  value       = oci_core_instance.ai_worker.public_ip
}

output "ssh_command" {
  description = "SSH command to reach the VM."
  value       = "ssh opc@${oci_core_instance.ai_worker.public_ip}"
}

output "tunnel_command" {
  description = "Forward Streamlit (8501) over SSH instead of opening to the world."
  value       = "ssh -L 8501:127.0.0.1:8501 opc@${oci_core_instance.ai_worker.public_ip}"
}

output "data_volume_device" {
  description = "Paravirtualised block volume id; appears on the instance as a /dev/oracleoci/oraclevd* device."
  value       = oci_core_volume_attachment.data.id
}
