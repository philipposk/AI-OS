variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "instance_type" {
  description = "EC2 instance type. t4g.small = 2 vCPU / 2 GB ARM (free-tier-ish; smallest sensible)."
  type        = string
  default     = "t4g.small"
}

variable "disk_size_gb" {
  type    = number
  default = 30
}

variable "ssh_public_key_path" {
  type = string
}

variable "expose_streamlit_publicly" {
  type    = bool
  default = false
}

variable "repo_url" {
  type    = string
  default = "https://github.com/philipposk/AI-OS.git"
}

variable "repo_branch" {
  type    = string
  default = "main"
}
