# redStack — Boot-to-Breach Lab on AWS

A self-contained red-team training environment that deploys 7 EC2 instances across two peered VPCs in ~45 minutes. Includes three C2 frameworks (Mythic, Sliver, Havoc), an Apache redirector, Kali and Windows workstations, and a Guacamole access portal.

## What It Does

redStack removes the infrastructure hurdle for learning adversarial operations. One `terraform apply` provisions a complete lab where you practice payload generation, beacon callbacks, C2 operations, and post-exploitation — without manually wiring up servers, security groups, or DNS.

### What Gets Deployed

| Hostname | Role | Public IP? |
|----------|------|-----------|
| `guac` | Guacamole portal (web SSH/RDP/VNC) | Yes (Elastic IP) |
| `redirector` | Apache reverse proxy + C2 frontend (80/443 only) | Yes (Elastic IP) |
| `mythic` | Mythic C2 server | No |
| `sliver` | Sliver C2 server | No |
| `havoc` | Havoc C2 server + desktop (VNC) | No |
| `windows` | Windows Server 2022 workstation | No |
| `kali` | Kali Linux workstation | No |

### Network Architecture

Two peered VPCs:
- **VPC A (TeamServer):** `10.50.0.0/16` — hosts guacamole, mythic, sliver, havoc, windows, kali
- **VPC B (Redirector):** `10.60.0.0/16` — hosts the Apache redirector, simulates an external VPS

C2 traffic flows: `Target -> Redirector EIP (80/443)` -> header+URI validation -> `C2 server private IP`

No header = decoy CDN maintenance page. Optional OpenVPN + WireGuard tunnel for cyber range (HTB/VulnLab) access.

---

## How to Use It — First Steps

### 0. Prerequisites (One-Time Setup)

1. **AWS Account** — use a dedicated throwaway account
2. **AWS CLI** installed and configured (`aws configure`)
3. **Terraform** >= 1.0 installed
4. **SSH Key Pair** — create in EC2 Console or via CLI:
   ```bash
   aws ec2 create-key-pair --key-name rs-rsa-key --query 'KeyMaterial' --output text > rs-rsa-key.pem
   ```
   Set permissions (Windows PowerShell):
   ```powershell
   icacls .\rs-rsa-key.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
   ```
5. **Kali Marketplace subscription** — visit https://aws.amazon.com/marketplace/pp/prodview-fznsw3f7mq7to and click "Continue to Subscribe" (one-time per account, free)
6. **Submit AWS Penetration Testing form** — https://aws.amazon.com/security/penetration-testing/

### 1. Configure Deployment

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and fill in at minimum:

```hcl
localPub_ip         = "<your-public-ip>/32"   # Run: curl ifconfig.me
ssh_key_name        = "rs-rsa-key"
ssh_private_key_path = "../rs-rsa-key.pem"
redirector_domain   = "c2.yourdomain.com"       # Or leave blank for example.com
```

(Optional) Adjust instance types, C2 URI prefixes, or toggle VPN tunnel mode.

### 2. Deploy

```bash
cd terraform
terraform init
terraform apply
```

Wait ~30-60 minutes for first deploy (cloud-init + Windows boot + Havoc build from source).

### 3. Get Your Credentials

```bash
terraform output deployment_info
```

This prints all IPs, URLs, usernames, and passwords. Also saved to `deployment_info.txt` at the repo root.

### 4. Access the Lab

1. Browse to **https://<guac-public-ip>/guacamole** — log in with `guacadmin` / `<random-password>`
2. From Guacamole, you can SSH to: **mythic**, **sliver**, **havoc**, **kali**, **redirector**
3. RDP to: **windows**

### 5. Land Your First Beacon

From the Windows workstation (via Guacamole RDP), generate and deploy C2 payloads:

- **Mythic:** https://mythic:7443 — `mythic_admin` / `<random-password>`
- **Sliver:** SSH to sliver, run `sliver` — operator config at `/home/admin/.sliver-client/configs/admin.cfg`
- **Havoc:** Connect via Havoc Desktop (VNC) in Guacamole — teamserver at `havoc:40056`

### 6. Clean Up

When done, destroy everything to avoid ongoing costs:

```bash
terraform destroy
```

---

## Key Files

| File | Purpose |
|------|---------|
| `terraform/main.tf` | VPC, subnets, Mythic, Guacamole, Windows |
| `terraform/variables.tf` | All input variables with defaults |
| `terraform/terraform.tfvars.example` | Configuration template |
| `terraform/security_groups.tf` | Per-host firewall rules |
| `terraform/setup_scripts/` | Cloud-init scripts for each host |
| `deployment_info.txt` | Credentials and IPs (generated after apply) |

## Cost

~$0.27/hr while running. With `terraform destroy` between sessions: ~$15-20/month for 5-10 hr/week study. **Always set a CloudWatch billing alarm.**
