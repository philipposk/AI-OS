variable "project_id" { type = string }
variable "region" { type = string, default = "europe-west2" }
variable "zone" { type = string, default = "europe-west2-a" }
variable "machine_type" {
  description = "GCE machine type. t2a-standard-1 = 1 vCPU / 4 GB ARM (cheapest sensible)."
  type        = string
  default     = "t2a-standard-1"
}
variable "disk_size_gb" { type = number, default = 30 }
variable "ssh_public_key_path" { type = string }
variable "expose_streamlit_publicly" { type = bool, default = false }
variable "repo_url" {
  type    = string
  default = "https://github.com/philipposk/AI-OS.git"
}
variable "repo_branch" { type = string, default = "main" }
