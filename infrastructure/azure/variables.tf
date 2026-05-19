variable "subscription_id" { type = string }
variable "location" { type = string, default = "westeurope" }
variable "vm_size" {
  description = "Azure VM size. Standard_D2pls_v5 = 2 vCPU / 4 GB ARM."
  type        = string
  default     = "Standard_D2pls_v5"
}
variable "disk_size_gb" { type = number, default = 30 }
variable "ssh_public_key_path" { type = string }
variable "expose_streamlit_publicly" { type = bool, default = false }
variable "repo_url" {
  type    = string
  default = "https://github.com/philipposk/AI-OS.git"
}
variable "repo_branch" { type = string, default = "main" }
