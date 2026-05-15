# Terraform - Infrastructure provisioning
provider "oracle" {
  region = "us-phx-1"
}

resource "oci_compute_instance" "ai_worker" {
  compartment_id = var.compartment_id
  availability_domain = "nzfR:US-PHX-AD-1"
  shape = "VM.Standard.E4.Flex"
  shape_config {
    ocpus = 4
    memory_in_gbs = 24
  }
}

resource "oci_blockstorage_volume" "data" {
  availability_domain = "nzfR:US-PHX-AD-1"
  compartment_id = var.compartment_id
  size_in_gbs = 200
}