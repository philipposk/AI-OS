terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

resource "google_compute_network" "main" {
  name                    = "ai-company-net"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "public" {
  name          = "ai-company-public"
  ip_cidr_range = "10.40.1.0/24"
  region        = var.region
  network       = google_compute_network.main.id
}

resource "google_compute_firewall" "ssh" {
  name    = "ai-company-ssh"
  network = google_compute_network.main.name
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "streamlit" {
  count   = var.expose_streamlit_publicly ? 1 : 0
  name    = "ai-company-streamlit"
  network = google_compute_network.main.name
  allow {
    protocol = "tcp"
    ports    = ["8501"]
  }
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_instance" "ai_worker" {
  name         = "ai-company"
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-arm64"
      size  = var.disk_size_gb
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.public.id
    access_config {}
  }

  metadata = {
    ssh-keys  = "ubuntu:${file(var.ssh_public_key_path)}"
    user-data = templatefile("${path.module}/../modules/cloud-init.yaml", {
      repo_url    = var.repo_url
      repo_branch = var.repo_branch
    })
  }
}
