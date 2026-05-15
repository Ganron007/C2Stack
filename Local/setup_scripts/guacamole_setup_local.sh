#!/bin/bash
# guacamole_setup_local.sh - Local VM version (no IMDS dependency)
set -e

exec >> /var/log/user-data.log 2>&1

echo "===== Guacamole Server Setup Started (Local VM) $(date) ====="

GUAC_ADMIN_PASSWORD="${GUAC_ADMIN_PASSWORD:-redStack2024!}"
WINDOWS_PRIVATE_IP="${WINDOWS_PRIVATE_IP:-10.50.0.30}"
WINDOWS_USERNAME="${WINDOWS_USERNAME:-Administrator}"
WINDOWS_PASSWORD="${WINDOWS_PASSWORD:-redStack2024!}"
SSH_PASSWORD="${SSH_PASSWORD:-redStack2024!}"
MYTHIC_PRIVATE_IP="${MYTHIC_PRIVATE_IP:-10.50.0.10}"
REDIRECTOR_PRIVATE_IP="${REDIRECTOR_PRIVATE_IP:-10.60.0.10}"
SLIVER_PRIVATE_IP="${SLIVER_PRIVATE_IP:-10.50.0.11}"
HAVOC_PRIVATE_IP="${HAVOC_PRIVATE_IP:-10.50.0.12}"
GUACAMOLE_PRIVATE_IP="${GUACAMOLE_PRIVATE_IP:-10.50.0.20}"
KALI_PRIVATE_IP="${KALI_PRIVATE_IP:-10.50.0.40}"
KALI_DEPLOYMENT_MODE="${KALI_DEPLOYMENT_MODE:-headless}"
PUBLIC_IP="${GUAC_PUBLIC_IP:-127.0.0.1}"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

apt-get install -y docker.io docker-compose nginx certbot python3-certbot-nginx curl postgresql-client jq

systemctl enable docker
systemctl start docker
usermod -aG docker vagrant

mkdir -p /opt/guacamole/{postgres,config}
cd /opt/guacamole

docker run --rm guacamole/guacamole /opt/guacamole/bin/initdb.sh --postgresql > initdb.sql

DB_PASSWORD=$(openssl rand -base64 16)

cat > docker-compose.yml <<EOF
version: '3'
services:
  guacd:
    image: guacamole/guacd
    container_name: guacd
    restart: unless-stopped
    volumes:
      - /drive:/drive
    networks:
      - guac-network
  postgres:
    image: postgres:15
    container_name: postgres_guacamole
    restart: unless-stopped
    environment:
      POSTGRES_DB: guacamole_db
      POSTGRES_USER: guacamole_user
      POSTGRES_PASSWORD: $DB_PASSWORD
    volumes:
      - ./postgres:/var/lib/postgresql/data
      - ./initdb.sql:/docker-entrypoint-initdb.d/initdb.sql
    networks:
      - guac-network
  guacamole:
    image: guacamole/guacamole
    container_name: guacamole
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      GUACD_HOSTNAME: guacd
      POSTGRESQL_HOSTNAME: postgres
      POSTGRESQL_DATABASE: guacamole_db
      POSTGRESQL_USER: guacamole_user
      POSTGRESQL_PASSWORD: $DB_PASSWORD
    volumes:
      - /drive:/drive
    depends_on:
      - guacd
      - postgres
    networks:
      - guac-network
networks:
  guac-network:
    driver: bridge
EOF

mkdir -p /drive
chmod 777 /drive

docker-compose up -d
sleep 10

cat > /etc/nginx/sites-available/guacamole <<EOF
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}
server {
    listen 443 ssl;
    server_name _;
    ssl_certificate /etc/ssl/certs/guacamole-selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/guacamole-selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    client_max_body_size 0;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_buffering off;
        proxy_http_version 1.1;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$http_connection;
        proxy_cookie_path /guacamole/ /;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        access_log off;
    }
}
EOF

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/guacamole-selfsigned.key \
    -out /etc/ssl/certs/guacamole-selfsigned.crt \
    -subj "/C=US/ST=Training/L=Training/O=RedTeam/CN=guacamole"

ln -sf /etc/nginx/sites-available/guacamole /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo "[*] Waiting for Guacamole API..."
MAX_RETRIES=30
RETRY_COUNT=0
TOKEN=""
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    RESPONSE=$(curl -s -X POST "http://localhost:8080/guacamole/api/tokens" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=guacadmin&password=guacadmin" 2>/dev/null) || true
    TOKEN=$(printf '%s' "$RESPONSE" | jq -r '.authToken // empty' 2>/dev/null) || TOKEN=""
    if [ -n "$TOKEN" ]; then
        echo "[+] Guacamole API ready after $((RETRY_COUNT * 10)) seconds"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 10
done

if [ -n "$TOKEN" ]; then
    PW_RESP=$(curl -s -X PUT "http://localhost:8080/guacamole/api/session/data/postgresql/users/guacadmin/password?token=$TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"oldPassword\":\"guacadmin\",\"newPassword\":\"$GUAC_ADMIN_PASSWORD\"}") || true

    RESPONSE=$(curl -s -X POST "http://localhost:8080/guacamole/api/tokens" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=guacadmin&password=$GUAC_ADMIN_PASSWORD" 2>/dev/null) || true
    TOKEN=$(printf '%s' "$RESPONSE" | jq -r '.authToken // empty' 2>/dev/null) || TOKEN=""

    if [ -z "$TOKEN" ]; then
        RESPONSE=$(curl -s -X POST "http://localhost:8080/guacamole/api/tokens" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "username=guacadmin&password=guacadmin" 2>/dev/null) || true
        TOKEN=$(printf '%s' "$RESPONSE" | jq -r '.authToken // empty' 2>/dev/null) || TOKEN=""
    fi

    if [ -n "$TOKEN" ]; then
        # Windows RDP connection
        curl -s -X POST "http://localhost:8080/guacamole/api/session/data/postgresql/connections?token=$TOKEN" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg host "$WINDOWS_PRIVATE_IP" --arg user "$WINDOWS_USERNAME" --arg pass "$WINDOWS_PASSWORD" '{
                name: "Windows (RDP)", protocol: "rdp",
                parameters: {hostname: $host, port: "3389", username: $user, password: $pass, security: "any", "ignore-cert": "true", "enable-drive": "true", "drive-name": "GuacShare", "drive-path": "/drive", "create-drive-path": "true", console: "true", "server-layout": "en-us-qwerty"},
                attributes: {"max-connections": "2", "max-connections-per-user": "1"}
            }')"

        # SSH connections
        for conn in "Mythic (SSH):$MYTHIC_PRIVATE_IP:22" "Redirector (SSH):$REDIRECTOR_PRIVATE_IP:22" "Sliver (SSH):$SLIVER_PRIVATE_IP:22" "Havoc (SSH):$HAVOC_PRIVATE_IP:22" "Kali (SSH):$KALI_PRIVATE_IP:22"; do
            name=$(echo "$conn" | cut -d: -f1)
            ip=$(echo "$conn" | cut -d: -f2)
            port=$(echo "$conn" | cut -d: -f3)
            curl -s -X POST "http://localhost:8080/guacamole/api/session/data/postgresql/connections?token=$TOKEN" \
                -H "Content-Type: application/json" \
                -d "$(jq -n --arg name "$name" --arg host "$ip" --arg port "$port" --arg pass "$SSH_PASSWORD" '{
                    name: $name, protocol: "ssh",
                    parameters: {hostname: $host, port: $port, username: "vagrant", password: $pass, "color-scheme": "green-black", "font-size": "12"},
                    attributes: {"max-connections": "2", "max-connections-per-user": "1"}
                }')"
        done

        # Guacamole self-SSH
        curl -s -X POST "http://localhost:8080/guacamole/api/session/data/postgresql/connections?token=$TOKEN" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg host "$GUACAMOLE_PRIVATE_IP" --arg pass "$SSH_PASSWORD" '{
                name: "Guacamole (SSH)", protocol: "ssh",
                parameters: {hostname: $host, port: "22", username: "vagrant", password: $pass, "color-scheme": "green-black", "font-size": "12"},
                attributes: {"max-connections": "2", "max-connections-per-user": "1"}
            }')"

        # Havoc VNC
        curl -s -X POST "http://localhost:8080/guacamole/api/session/data/postgresql/connections?token=$TOKEN" \
            -H "Content-Type: application/json" \
            -d "$(jq -n --arg host "$HAVOC_PRIVATE_IP" --arg pass "$SSH_PASSWORD" '{
                name: "Havoc Desktop (VNC)", protocol: "vnc",
                parameters: {hostname: $host, port: "5901", password: $pass, "color-depth": "24"},
                attributes: {"max-connections": "2", "max-connections-per-user": "1"}
            }')"

        # Kali XRDP (if GUI mode)
        if [ "$KALI_DEPLOYMENT_MODE" = "gui" ]; then
            curl -s -X POST "http://localhost:8080/guacamole/api/session/data/postgresql/connections?token=$TOKEN" \
                -H "Content-Type: application/json" \
                -d "$(jq -n --arg host "$KALI_PRIVATE_IP" --arg pass "$SSH_PASSWORD" '{
                    name: "Kali (XRDP)", protocol: "rdp",
                    parameters: {hostname: $host, port: "3389", username: "vagrant", password: $pass, security: "any", "ignore-cert": "true", "color-depth": "24"},
                    attributes: {"max-connections": "2", "max-connections-per-user": "1"}
                }')"
        fi
    fi
fi

echo "===== Guacamole Server Setup Completed ====="
echo "===== Access Guacamole at https://localhost:8443/guacamole ====="
echo "===== Default credentials: guacadmin / $GUAC_ADMIN_PASSWORD ====="
