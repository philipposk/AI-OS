terraform {
  required_version = ">= 1.5"
  required_providers {
    hcloud = { source = "hetznercloud/hcloud", version = "~> 1.45" }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "main" {
  name       = "ai-company"
  public_key = file(var.ssh_public_key_path)
}

resource "hcloud_firewall" "main" {
  name = "ai-company-fw"
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  dynamic "rule" {
    for_each = var.expose_streamlit_publicly ? [1] : []
    content {
      direction  = "in"
      protocol   = "tcp"
      port       = "8501"
      source_ips = ["0.0.0.0/0", "::/0"]
    }
  }
}

resource "hcloud_server" "ai_worker" {
  name        = "ai-company"
  image       = "ubuntu-24.04"
  server_type = var.server_type
  location    = var.location

  ssh_keys     = [hcloud_ssh_key.main.id]
  firewall_ids = [hcloud_firewall.main.id]

  user_data = templatefile("${path.module}/../modules/cloud-init.yaml", {
    repo_url    = var.repo_url
    repo_branch = var.repo_branch
  })
}
