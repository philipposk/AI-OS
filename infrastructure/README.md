# infrastructure/

Single Streamlit dashboard + CLI deployed to one of six cloud targets,
all sharing the same cloud-init bootstrap.

## Layout

```
infrastructure/
├── modules/
│   ├── cloud-init.yaml     # shared bootstrap (clones repo, builds venv, systemd unit)
│   └── deploy.sh           # local-machine helper
├── oracle/                 # OCI Always-Free Ampere A1 (original target)
├── aws/                    # EC2 t4g.small (ARM, cheapest sensible)
├── gcp/                    # GCE t2a-standard-1 (ARM)
├── azure/                  # Standard_D2pls_v5 (ARM)
├── hetzner/                # cax11 (~€4/mo ARM, often the best price/perf)
└── digitalocean/           # s-1vcpu-2gb droplet
```

Each cloud folder is self-contained: `terraform init && terraform plan
&& terraform apply` from inside it. Pick whichever cloud you have keys
for.

## Pick a cloud

Cheapest-to-most-flexible, roughly:

| Cloud | Monthly cost | Notes |
|---|---|---|
| **Oracle Cloud** | $0 (Always-Free Ampere A1, 4 OCPU / 24 GB) | Best free option; provisioning is finicky and capacity is regional. |
| **Hetzner**      | ~€4 (cax11 ARM 2 vCPU / 4 GB) | Best paid price/perf in EU. |
| **AWS**          | ~$15 (t4g.small ARM) | Largest ecosystem. |
| **DigitalOcean** | ~$12 (s-1vcpu-2gb) | Simplest API. |
| **GCP**          | ~$15 (t2a-standard-1 ARM) | Free $300 trial covers months. |
| **Azure**        | ~$30 (Standard_D2pls_v5 ARM) | If you're already on Microsoft. |

## Quickstart (Hetzner example)

```bash
cd infrastructure/hetzner
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars       # hcloud_token + ssh_public_key_path
terraform init
terraform plan
terraform apply

# Tunnel Streamlit
ssh -L 8501:127.0.0.1:8501 root@$(terraform output -raw public_ip)
# Now open http://localhost:8501
```

## Shared cloud-init

Every cloud folder references `../modules/cloud-init.yaml`. It:

1. Installs `git`, `python3.11`, `python3.11-pip`, `sqlite`.
2. Clones `$repo_url` at `$repo_branch` into `/opt/ai-company` as the
   distro's default user.
3. Builds a venv + installs `requirements.txt`.
4. Writes `/etc/systemd/system/ai-company.service` and enables it.
5. Drops `/usr/local/bin/ai-company-update.sh` for one-line redeploy.

The user-data variables (`repo_url`, `repo_branch`) are templated by
each provider's main.tf via Terraform's `templatefile()`.

## Notes

- Default ssh access only; flip `expose_streamlit_publicly = true` to
  open `:8501` to the world. Don't, in production — use the SSH tunnel.
- Every module assumes the bootstrap user is `ubuntu` (AWS/GCP/Azure/DO)
  or `root` (Hetzner). The systemd unit runs as the distro default user.
- ARM is preferred everywhere because the cloud-init uses
  `python3.11-pip` from the distro repos, which is available on both
  arches but ARM is cheaper.
