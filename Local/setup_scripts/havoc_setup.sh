#!/bin/bash
# havoc_setup.sh - Local VM version
set -e

exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "===== Havoc C2 Server Setup Started (Local VM) $(date) ====="

SSH_PASSWORD="${SSH_PASSWORD:-redStack2024!}"
MAIN_VPC_CIDR="${MAIN_VPC_CIDR:-10.50.0.0/16}"
REDIRECTOR_VPC_CIDR="${REDIRECTOR_VPC_CIDR:-10.60.0.0/16}"

hostnamectl set-hostname havoc

cat >> /etc/hosts << HOSTS

# redStack lab hosts
${HAVOC_PRIVATE_IP:-10.50.0.12}      havoc
${GUACAMOLE_PRIVATE_IP:-10.50.0.20}  guac
${MYTHIC_PRIVATE_IP:-10.50.0.10}     mythic
${SLIVER_PRIVATE_IP:-10.50.0.11}     sliver
${REDIRECTOR_PRIVATE_IP:-10.60.0.10} redirector
${WINDOWS_PRIVATE_IP:-10.50.0.30}    windows
${KALI_PRIVATE_IP:-10.50.0.40}       kali
HOSTS

echo "vagrant:$SSH_PASSWORD" | chpasswd
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git build-essential cmake nasm mingw-w64 curl wget ufw net-tools jq \
    python3 python3-pip python3-dev libssl-dev \
    xfce4 xfce4-terminal tigervnc-standalone-server dbus-x11 \
    libqt5websockets5 libqt5websockets5-dev qtbase5-dev qtchooser qt5-qmake \
    qtbase5-dev-tools qtdeclarative5-dev libqt5svg5-dev libfontconfig1-dev \
    libglu1-mesa-dev libgtest-dev libspdlog-dev libboost-all-dev

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 80 proto tcp
ufw allow from $REDIRECTOR_VPC_CIDR to any port 443 proto tcp
ufw allow 40056/tcp
ufw allow from $MAIN_VPC_CIDR to any port 5901 proto tcp
ufw --force enable

mkdir -p /home/vagrant/.havoc
cat > /home/vagrant/.havoc/default.yaotl << PROFILE
Teamserver {
    Host = "0.0.0.0"
    Port = 40056
    Build {
        Compiler64 = "/usr/bin/x86_64-w64-mingw32-gcc"
        Compiler86 = "/usr/bin/i686-w64-mingw32-gcc"
        Nasm       = "/usr/bin/nasm"
    }
}
Demon {
    Sleep    = 2
    Jitter   = 0
    TrustXForwardedFor = false
}
Operators {
    user "admin" {
        Password = "$SSH_PASSWORD"
    }
}
PROFILE

mkdir -p /home/vagrant/.vnc
printf '%s\n' "$SSH_PASSWORD" | vncpasswd -f > /home/vagrant/.vnc/passwd
chmod 600 /home/vagrant/.vnc/passwd

cat > /home/vagrant/.vnc/xstartup << 'XSTART'
#!/bin/bash
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec startxfce4
XSTART
chmod +x /home/vagrant/.vnc/xstartup

mkdir -p /home/vagrant/.config/autostart
cat > /home/vagrant/.config/autostart/havoc-client.desktop << 'AUTOSTART'
[Desktop Entry]
Type=Application
Name=Havoc C2 Client
Exec=havoc-client client
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
AUTOSTART

cat > /etc/systemd/system/havoc.service << 'SVCEOF'
[Unit]
Description=Havoc C2 Teamserver
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/Havoc
ExecStart=/opt/Havoc/teamserver/teamserver server --profile /opt/Havoc/profiles/default.yaotl
User=vagrant
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
SVCEOF

cat > /etc/systemd/system/vncserver@.service << 'VNCSVC'
[Unit]
Description=TigerVNC Desktop :%i
After=network.target
[Service]
Type=forking
User=vagrant
WorkingDirectory=/home/vagrant
ExecStartPre=-/usr/bin/vncserver -kill :%i > /dev/null 2>&1
ExecStart=/usr/bin/vncserver :%i -geometry 1280x800 -depth 24 -localhost no
ExecStop=/usr/bin/vncserver -kill :%i
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
VNCSVC

cat > /home/vagrant/build_havoc.sh << 'BUILDSCRIPT'
#!/bin/bash
set -e
exec > >(tee /home/vagrant/havoc_build.log) 2>&1
echo "===== Havoc Build Started $(date) ====="
echo "[*] Estimated time: 15-25 minutes"

GO_VERSION="1.22.5"
if /usr/local/go/bin/go version 2>/dev/null | grep -q "$GO_VERSION"; then
    echo "[*] Go $GO_VERSION already installed"
else
    wget -q "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
fi

export PATH=$PATH:/usr/local/go/bin
export GOPATH=$HOME/go
export PATH=$PATH:$GOPATH/bin

if [ -d "/opt/Havoc/.git" ]; then
    echo "[*] /opt/Havoc already cloned"
else
    HAVOC_TAG=$(curl -sL https://api.github.com/repos/HavocFramework/Havoc/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
    [ -z "$HAVOC_TAG" ] && HAVOC_TAG="main"
    sudo git clone --branch "$HAVOC_TAG" https://github.com/HavocFramework/Havoc.git /opt/Havoc
    sudo chown -R vagrant:vagrant /opt/Havoc
fi

sudo mkdir -p /opt/Havoc/profiles /opt/Havoc/teamserver/data
sudo cp /home/vagrant/.havoc/default.yaotl /opt/Havoc/profiles/default.yaotl
sudo chown -R vagrant:vagrant /opt/Havoc

cd /opt/Havoc/teamserver
/usr/local/go/bin/go build -buildvcs=false -o teamserver .

cd /opt/Havoc
git submodule update --init --recursive
mkdir -p client/Build
cd client/Build
cmake ..
cmake --build /opt/Havoc/client/Build -- -j$(nproc)

sudo tee /usr/local/bin/havoc-client > /dev/null << 'WRAPPER'
#!/bin/bash
cd /opt/Havoc
exec /opt/Havoc/client/Havoc "$@"
WRAPPER
sudo chmod +x /usr/local/bin/havoc-client

sudo chown -R vagrant:vagrant /opt/Havoc
sudo setcap 'cap_net_bind_service=+ep' /opt/Havoc/teamserver/teamserver

sudo systemctl daemon-reload
sudo systemctl enable havoc.service
sudo systemctl start havoc.service

echo ""
echo "===== Havoc Build Complete ====="
echo "  Teamserver: localhost:40056"
echo "  Client:     havoc-client client"
echo "  VNC:        connect via Guacamole — client autostarts"
BUILDSCRIPT
chmod +x /home/vagrant/build_havoc.sh

systemctl daemon-reload
systemctl enable havoc.service
systemctl enable vncserver@1.service
systemctl start vncserver@1.service || true

echo "===== Havoc C2 Server Setup Completed $(date) ====="
echo "[+] Run ~/build_havoc.sh to build Havoc (15-25 min)"
