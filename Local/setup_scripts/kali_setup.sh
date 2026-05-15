#!/bin/bash
# kali_setup.sh - Local VM version
set -e
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "===== Kali Operator Setup Started (Local VM) $(date) ====="

SSH_PASSWORD="${SSH_PASSWORD:-redStack2024!}"
KALI_MODE="${KALI_MODE:-headless}"

if id kali >/dev/null 2>&1; then
    usermod -l vagrant -d /home/vagrant -m kali
    groupmod -n vagrant kali 2>/dev/null || true
    if [ -f /etc/sudoers.d/90-cloud-init-users ]; then
        sed -i 's/\bkali\b/vagrant/g' /etc/sudoers.d/90-cloud-init-users
    fi
fi

hostnamectl set-hostname kali

cat >> /etc/hosts << HOSTS

# redStack lab hosts
${KALI_PRIVATE_IP:-10.50.0.40}       kali
${GUACAMOLE_PRIVATE_IP:-10.50.0.20}  guac
${MYTHIC_PRIVATE_IP:-10.50.0.10}     mythic
${SLIVER_PRIVATE_IP:-10.50.0.11}     sliver
${HAVOC_PRIVATE_IP:-10.50.0.12}      havoc
${REDIRECTOR_PRIVATE_IP:-10.60.0.10} redirector
${WINDOWS_PRIVATE_IP:-10.50.0.30}    windows
HOSTS

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl wget git net-tools ufw jq ca-certificates openssh-server

echo "vagrant:$SSH_PASSWORD" | chpasswd
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart ssh

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 3389/tcp
ufw --force enable

# install-kali-tools helper
cat > /usr/local/sbin/install-kali-tools << 'TOOLSCRIPT'
#!/bin/bash
if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run as root (sudo install-kali-tools)" >&2; exit 1; fi

PACKAGES=(nmap enum4linux-ng smbmap mitm6 seclists gobuster coercer ldap-utils
          impacket-scripts netexec evil-winrm bloodhound.py certipy-ad responder
          hashcat john pipx proxychains4)

echo "[*] Installing ${#PACKAGES[@]} Kali tools..."
apt-get update
for pkg in "${PACKAGES[@]}"; do
    echo "----- $pkg -----"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" || echo "[!] $pkg failed"
done

export PATH="$PATH:/root/.local/bin"
pipx install --force adidnsdump > /dev/null 2>&1 || true
pipx install --force "git+https://github.com/garrettfoster13/pre2k" > /dev/null 2>&1 || true

KERBRUTE_URL=$(curl -sf https://api.github.com/repos/ropnop/kerbrute/releases/latest | jq -r '.assets[] | select(.name == "kerbrute_linux_amd64") | .browser_download_url')
[ -n "$KERBRUTE_URL" ] && wget -q "$KERBRUTE_URL" -O /usr/local/bin/kerbrute && chmod 755 /usr/local/bin/kerbrute || true

echo "[+] Kali tools installed (some may have failed — re-run to retry)"
TOOLSCRIPT
chmod 755 /usr/local/sbin/install-kali-tools

echo "[*] Running install-kali-tools..."
/usr/local/sbin/install-kali-tools || true

# GUI conversion helper
cat > /usr/local/sbin/kali-go-gui << 'GUISCRIPT'
#!/bin/bash
if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run as root (sudo kali-go-gui)" >&2; exit 1; fi
echo "[*] Installing XFCE desktop and XRDP..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y kali-desktop-xfce xrdp
cat > /etc/xrdp/startwm.sh << 'STARTWM'
#!/bin/sh
if test -r /etc/profile; then . /etc/profile; fi
if test -r ~/.profile; then . ~/.profile; fi
exec startxfce4
STARTWM
chmod +x /etc/xrdp/startwm.sh
systemctl enable xrdp
systemctl restart xrdp
echo "[+] GUI active. Register XRDP in Guacamole via guacamole VM."
GUISCRIPT
chmod 755 /usr/local/sbin/kali-go-gui

# GUI mode at deploy time
if [ "$KALI_MODE" = "gui" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y kali-desktop-xfce xrdp
    cat > /etc/xrdp/startwm.sh << 'STARTWM'
#!/bin/sh
if test -r /etc/profile; then . /etc/profile; fi
if test -r ~/.profile; then . ~/.profile; fi
exec startxfce4
STARTWM
    chmod +x /etc/xrdp/startwm.sh
    systemctl enable xrdp
    systemctl restart xrdp
fi

echo "===== Kali Operator Setup Completed $(date) ====="
