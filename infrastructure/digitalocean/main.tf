terraform {
  required_version = ">= 1.5"
  required_providers {
    digitalocean = { source = "digitalocean/digitalocean", version = "~> 2.40" }
  }
}

provider "digitalocean" {
  token = var.do_token
}

resource "digitalocean_ssh_key" "main" {
  name       = "ai-company"
  public_key = file(var.ssh_public_key_path)
}

resource "digitalocean_droplet" "ai_worker" {
  name   = "ai-company"
  image  = "ubuntu-24-04-x64"
  region = var.region
  size   = var.droplet_size

  ssh_keys = [digitalocean_ssh_key.main.fingerprint]

  user_data = templatefile("${path.module}/../modules/cloud-init.yaml", {
    repo_url    = var.repo_url
    repo_branch = var.repo_branch
  })
}

resource "digitalocean_firewall" "main" {
  name = "ai-company-fw"

  droplet_ids = [digitalocean_droplet.ai_worker.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  dynamic "inbound_rule" {
    for_each = var.expose_streamlit_publicly ? [1] : []
    content {
      protocol         = "tcp"
      port_range       = "8501"
      source_addresses = ["0.0.0.0/0", "::/0"]
    }
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
