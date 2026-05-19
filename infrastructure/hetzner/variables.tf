variable "hcloud_token" { type = string, sensitive = true }
variable "location" { type = string, default = "fsn1" }
variable "server_type" {
  description = "Hetzner server type. cax11 = 2 ARM vCPU / 4 GB / ~€4/mo. cx22 = x86 2 vCPU / 4 GB."
  type        = string
  default     = "cax11"
}
variable "ssh_public_key_path" { type = string }
variable "expose_streamlit_publicly" { type = bool, default = false }
variable "repo_url" {
  type    = string
  default = "https://github.com/philipposk/AI-OS.git"
}
variable "repo_branch" { type = string, default = "main" }
