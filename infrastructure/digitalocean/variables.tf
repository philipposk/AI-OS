variable "do_token" { type = string, sensitive = true }
variable "region" { type = string, default = "ams3" }
variable "droplet_size" {
  description = "DO droplet size. s-1vcpu-2gb = ~$12/mo (cheapest sensible)."
  type        = string
  default     = "s-1vcpu-2gb"
}
variable "ssh_public_key_path" { type = string }
variable "expose_streamlit_publicly" { type = bool, default = false }
variable "repo_url" {
  type    = string
  default = "https://github.com/philipposk/AI-OS.git"
}
variable "repo_branch" { type = string, default = "main" }
