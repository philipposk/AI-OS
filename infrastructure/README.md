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

## Phase Z / Z2 — self-improvement on cloud deploys

The new layers write state to disk. Make sure `data/` is on a
volume that survives reboots and re-deploys. Default cloud-init
puts the repo at `/opt/ai-company` and writes to `/opt/ai-company/data/`,
which lives on the root volume — fine for single-node, but back it up.

Paths the new layers write to (relative to `/opt/ai-company`):

| Path | Written by | Why it matters |
|---|---|---|
| `data/langgraph.sqlite`   | LangGraph SqliteSaver       | In-progress workflows. Losing it = users stuck at checkpoints can't resume. |
| `data/learned_prompts.json` | `cli.py tune auto`        | DSPy-tuned prompts. Losing it = revert to base prompts (safe, just dumber). |
| `data/auto_state.json`    | `cli.py tune auto`          | Watermark — last retrospective count per task_type. Losing it = next tune pulls the full history again (waste, not wrong). |
| `data/ai_company.sqlite`  | accounting + retrospectives | The historical signal that feeds the tuner. **Back this up.** |

### Cron the tuner

Add to the systemd unit or via crontab:

```cron
0 3 * * * cd /opt/ai-company && .venv/bin/python cli.py tune auto >> data/tune.log 2>&1
```

The tuner is idempotent and cheap when nothing changed; safe to run
hourly if you want faster adaptation.

### Backup / restore

Single command:

```bash
tar -czf ai-company-backup-$(date +%F).tar.gz \
    /opt/ai-company/data/ai_company.sqlite \
    /opt/ai-company/data/langgraph.sqlite \
    /opt/ai-company/data/learned_prompts.json \
    /opt/ai-company/data/auto_state.json
```

Stash to S3 / GCS / Backblaze as part of the nightly cron.

### Activating on a cloud deploy

After `terraform apply`, SSH in and edit `/opt/ai-company/.env`. The
relevant block is at the bottom of the templated `.env` (mirrors
`.env.example`). Uncomment the flags you want, then:

```bash
sudo systemctl restart ai-company
```

All new layers default-off — a vanilla deploy behaves identically to
pre-Phase-Z. Opt in per layer as you trust each one.
