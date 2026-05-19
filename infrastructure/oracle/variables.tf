variable "tenancy_ocid" {
  description = "OCI tenancy OCID."
  type        = string
}

variable "user_ocid" {
  description = "OCI user OCID for the API key. (Use `oci setup config` if unsure.)"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the OCI API public key."
  type        = string
}

variable "private_key_path" {
  description = "Path to OCI API private key (PEM)."
  type        = string
}

variable "region" {
  description = "OCI region, e.g. `us-phoenix-1`, `eu-frankfurt-1`."
  type        = string
  default     = "us-phoenix-1"
}

variable "compartment_id" {
  description = "OCID of the compartment to create resources in."
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key authorised to log in to the VM."
  type        = string
}

variable "instance_shape" {
  description = "Compute shape. Default = Ampere A1 Flex (Always-Free eligible)."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "OCPUs (1-4 free)."
  type        = number
  default     = 4
}

variable "instance_memory_gbs" {
  description = "Memory in GB (up to 24 free across all A1 instances combined)."
  type        = number
  default     = 24
}

variable "block_volume_size_gbs" {
  description = "Persistent data volume."
  type        = number
  default     = 100
}

variable "expose_streamlit_publicly" {
  description = "Open TCP 8501 to the world. Leave false in production; use an SSH tunnel."
  type        = bool
  default     = false
}

variable "repo_url" {
  description = "Git URL the VM clones at first boot."
  type        = string
  default     = "https://github.com/philipposk/AI-OS.git"
}

variable "repo_branch" {
  description = "Branch to deploy."
  type        = string
  default     = "main"
}
