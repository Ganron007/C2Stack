#!/bin/bash
# mythic_setup.sh - Local VM version (adapted from AWS, no IMDS dependency)
set -e

exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "===== Mythic Team Server Setup Started (Local VM) $(date) ====="

# Variables from Vagrantfile environment
LOCAL_PUB_IP="${LOCAL_PUB_IP:-}"
ENABLE_AUTOSTART="${ENABLE_AUTOSTART:-true}"
SSH_PASSWORD="${SSH_PASSWORD:-redStack2024!}"
VPC_CIDR="${VPC_CIDR:-10.50.0.0/16}"
REDIRECTOR_VPC_CIDR="${REDIRECTOR_VPC_CIDR:-10.60.0.0/16}"
MYTHIC_ADMIN_PASSWORD="${MYTHIC_ADMIN_PASSWORD:-redStack2024!}"

hostnamectl set-hostname mythic

cat >> /etc/hosts << HOSTS

# redStack lab hosts
${MYTHIC_PRIVATE_IP:-10.50.0.10}     mythic
${GUACAMOLE_PRIVATE_IP:-10.50.0.20}  guac
${SLIVER_PRIVATE_IP:-10.50.0.11}     sliver
${HAVOC_PRIVATE_IP:-10.50.0.12}      havoc
${REDIRECTOR_PRIVATE_IP:-10.60.0.10} redirector
${WINDOWS_PRIVATE_IP:-10.50.0.30}    windows
${KALI_PRIVATE_IP:-10.50.0.40}       kali
HOSTS

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

apt-get install -y docker.io make git curl ufw jq python3-pip

systemctl enable docker
systemctl start docker
usermod -aG docker vagrant

echo "vagrant:$SSH_PASSWORD" | chpasswd

# SSH: password auth enabled for local network
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd

mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow from $VPC_CIDR to any port 22 proto tcp
ufw allow from $VPC_CIDR to any port 7443:7444 proto tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 80 proto tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 443 proto tcp
ufw --force enable

cd /opt
git clone https://github.com/its-a-feature/Mythic
chown -R vagrant:vagrant Mythic
cd Mythic
make
./mythic-cli config set MYTHIC_ADMIN_PASSWORD "$MYTHIC_ADMIN_PASSWORD"
echo "[*] Starting Mythic (this will take 3-5 minutes)..."
./mythic-cli start
sleep 180
./mythic-cli status

echo "[*] Installing HTTP C2 profile and Apollo agent..."
./mythic-cli install github https://github.com/MythicC2Profiles/http
./mythic-cli install github https://github.com/MythicAgents/apollo

./mythic-cli stop
sleep 10
./mythic-cli start
sleep 60

pip3 install mythic --break-system-packages

if [ "$ENABLE_AUTOSTART" = "true" ]; then
    cat > /etc/systemd/system/mythic.service <<EOF
[Unit]
Description=Mythic C2 Framework
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/Mythic
ExecStart=/opt/Mythic/mythic-cli start
ExecStop=/opt/Mythic/mythic-cli stop
User=vagrant
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable mythic.service
fi

echo "===== Mythic Team Server Setup Completed $(date) ====="
