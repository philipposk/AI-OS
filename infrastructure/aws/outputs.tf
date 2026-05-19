output "public_ip" {
  value = aws_instance.ai_worker.public_ip
}

output "ssh_command" {
  value = "ssh ubuntu@${aws_instance.ai_worker.public_ip}"
}

output "tunnel_command" {
  value = "ssh -L 8501:127.0.0.1:8501 ubuntu@${aws_instance.ai_worker.public_ip}"
}
