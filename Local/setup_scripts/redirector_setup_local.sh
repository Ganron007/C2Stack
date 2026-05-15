#!/bin/bash
# redirector_setup_local.sh - Local VM version (no IMDS, uses env vars)
set -e

echo "===== Apache Redirector Setup Started (Local VM) $(date) ====="

MYTHIC_PRIVATE_IP="${MYTHIC_PRIVATE_IP:-10.50.0.10}"
SLIVER_PRIVATE_IP="${SLIVER_PRIVATE_IP:-10.50.0.11}"
HAVOC_PRIVATE_IP="${HAVOC_PRIVATE_IP:-10.50.0.12}"
DOMAIN_NAME="${DOMAIN_NAME:-}"
MYTHIC_URI_PREFIX="${MYTHIC_URI_PREFIX:-/cdn/media/stream}"
SLIVER_URI_PREFIX="${SLIVER_URI_PREFIX:-/cloud/storage/objects}"
HAVOC_URI_PREFIX="${HAVOC_URI_PREFIX:-/edge/cache/assets}"
C2_HEADER_NAME="${C2_HEADER_NAME:-X-Request-ID}"
C2_HEADER_VALUE="${C2_HEADER_VALUE:-redstack-local-dev}"
ENABLE_VPN="${ENABLE_VPN:-false}"
ENABLE_REDIRECT_RULES="${ENABLE_REDIRECT_RULES:-true}"
MAIN_VPC_CIDR="${MAIN_VPC_CIDR:-10.50.0.0/16}"
PUBLIC_IP="${REDIRECTOR_PUBLIC_IP:-127.0.0.1}"

echo "Public IP for SSL: $PUBLIC_IP"

if [ -z "$DOMAIN_NAME" ]; then
    DOMAIN_NAME="$PUBLIC_IP"
    CERT_CN="$PUBLIC_IP"
    CERT_SAN="IP:$PUBLIC_IP"
    NO_DOMAIN=true
else
    CERT_CN="$DOMAIN_NAME"
    CERT_SAN="DNS:$DOMAIN_NAME,IP:$PUBLIC_IP"
    NO_DOMAIN=false
fi

cat > /root/test_redirector.sh << 'TESTSCRIPT'
#!/bin/bash
echo "===== Redirector Connectivity Test ====="
systemctl status apache2 --no-pager
echo ""
apache2ctl -M 2>/dev/null | grep -E "proxy|rewrite|ssl|headers|deflate"
echo ""
apache2ctl -S 2>/dev/null
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
echo "[*] Testing decoy page:"
curl -s -A "$UA" http://localhost/ | head -5
echo ""
echo "[*] Testing C2 routing WITH header:"
curl -v -A "$UA" -H "HEADER_NAME_PH: HEADER_VALUE_PH" http://localhostMYTHIC_PREFIX_PH/ 2>&1 | head -15
echo ""
echo "[*] Testing C2 WITHOUT header:"
curl -v -A "$UA" http://localhostMYTHIC_PREFIX_PH/ 2>&1 | head -15
echo ""
echo "[*] Header: HEADER_NAME_PH: HEADER_VALUE_PH"
echo "[*] URI: MYTHIC_PREFIX_PH/ -> Mythic (MYTHIC_IP_PH)"
echo "[*] URI: SLIVER_PREFIX_PH/ -> Sliver (SLIVER_IP_PH)"
echo "[*] URI: HAVOC_PREFIX_PH/ -> Havoc (HAVOC_IP_PH)"
TESTSCRIPT
sed -i "s|MYTHIC_IP_PH|$MYTHIC_PRIVATE_IP|g" /root/test_redirector.sh
sed -i "s|SLIVER_IP_PH|$SLIVER_PRIVATE_IP|g" /root/test_redirector.sh
sed -i "s|HAVOC_IP_PH|$HAVOC_PRIVATE_IP|g" /root/test_redirector.sh
sed -i "s|MYTHIC_PREFIX_PH|$MYTHIC_URI_PREFIX|g" /root/test_redirector.sh
sed -i "s|SLIVER_PREFIX_PH|$SLIVER_URI_PREFIX|g" /root/test_redirector.sh
sed -i "s|HAVOC_PREFIX_PH|$HAVOC_URI_PREFIX|g" /root/test_redirector.sh
sed -i "s|HEADER_NAME_PH|$C2_HEADER_NAME|g" /root/test_redirector.sh
sed -i "s|HEADER_VALUE_PH|$C2_HEADER_VALUE|g" /root/test_redirector.sh
chmod +x /root/test_redirector.sh

apt-get update
apt-get install -y apache2 openssl curl ufw net-tools
apt-get install -y libssl-dev ca-certificates openjdk-17-jdk

systemctl enable apache2
a2enmod rewrite ssl proxy proxy_http proxy_connect headers deflate proxy_balancer proxy_html lbmethod_byrequests
a2dismod autoindex -f
systemctl restart apache2

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

mkdir -p /etc/apache2/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/apache2/ssl/redirector.key \
    -out /etc/apache2/ssl/redirector.crt \
    -subj "/C=US/ST=State/L=City/O=Company/CN=$CERT_CN" \
    -addext "subjectAltName=$CERT_SAN"

# Decoy page
mkdir -p /var/www/html/decoy
cat > /var/www/html/decoy/index.html << 'DECOYHTML'
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>CloudEdge CDN - Service Portal</title>
<style>
body{font-family:'Segoe UI',Arial,sans-serif;background:#f4f4f4;color:#333;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.container{text-align:center;background:white;padding:60px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,0.1);max-width:500px}
h1{color:#2c3e50;font-size:24px;margin-bottom:10px}
p{color:#7f8c8d;font-size:14px;line-height:1.6}
.status{margin-top:20px;padding:10px;background:#eaf7ea;border-radius:4px;color:#27ae60;font-size:13px}
</style></head><body><div class="container">
<h1>CloudEdge CDN</h1><p>Content delivery service is currently undergoing scheduled maintenance.</p>
<div class="status">System Status: Maintenance Window Active</div>
</div></body></html>
DECOYHTML

# HTTP VirtualHost
cat > /etc/apache2/sites-available/redirector-http.conf << APACHECONF
<VirtualHost *:80>
    ServerName DOMAIN_PLACEHOLDER
    DocumentRoot /var/www/html/decoy
    RewriteEngine On
    LogLevel warn
    ErrorLog /var/log/apache2/redirector-error.log
    CustomLog /var/log/apache2/redirector-access.log combined
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-For "%{REMOTE_ADDR}e"

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^MYTHIC_PREFIX_PH/(.*) http://MYTHIC_IP_PH/$1 [P,L]
    ProxyPassReverse MYTHIC_PREFIX_PH/ http://MYTHIC_IP_PH/

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^SLIVER_PREFIX_PH/(.*) http://SLIVER_IP_PH/$1 [P,L]
    ProxyPassReverse SLIVER_PREFIX_PH/ http://SLIVER_IP_PH/

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^(HAVOC_PREFIX_PH/.*) http://HAVOC_IP_PH$1 [P,L]
    ProxyPassReverse HAVOC_PREFIX_PH/ http://HAVOC_IP_PH/

    <Directory /var/www/html/decoy>
        AllowOverride All
        Options -Indexes
        Require all granted
    </Directory>
</VirtualHost>
APACHECONF

# HTTPS VirtualHost
cat > /etc/apache2/sites-available/redirector-https.conf << APACHECONF
<VirtualHost *:443>
    ServerName DOMAIN_PLACEHOLDER
    DocumentRoot /var/www/html/decoy
    SSLEngine on
    SSLCertificateFile /etc/apache2/ssl/redirector.crt
    SSLCertificateKeyFile /etc/apache2/ssl/redirector.key
    RewriteEngine On
    ErrorLog /var/log/apache2/redirector-ssl-error.log
    CustomLog /var/log/apache2/redirector-ssl-access.log combined
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-For "%{REMOTE_ADDR}e"

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^MYTHIC_PREFIX_PH/(.*) http://MYTHIC_IP_PH/$1 [P,L]
    ProxyPassReverse MYTHIC_PREFIX_PH/ http://MYTHIC_IP_PH/

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^SLIVER_PREFIX_PH/(.*) http://SLIVER_IP_PH/$1 [P,L]
    ProxyPassReverse SLIVER_PREFIX_PH/ http://SLIVER_IP_PH/

    RewriteCond %{HTTP:HEADER_NAME_PH} ^HEADER_VALUE_PH$
    RewriteRule ^(HAVOC_PREFIX_PH/.*) http://HAVOC_IP_PH$1 [P,L]
    ProxyPassReverse HAVOC_PREFIX_PH/ http://HAVOC_IP_PH/

    <Directory /var/www/html/decoy>
        AllowOverride All
        Options -Indexes
        Require all granted
    </Directory>
    SSLProxyEngine On
    SSLProxyVerify none
    SSLProxyCheckPeerCN off
    SSLProxyCheckPeerName off
</VirtualHost>
APACHECONF

# Replace placeholders
for f in /etc/apache2/sites-available/redirector-http.conf /etc/apache2/sites-available/redirector-https.conf; do
    sed -i "s|DOMAIN_PLACEHOLDER|$DOMAIN_NAME|g" "$f"
    sed -i "s|MYTHIC_IP_PH|$MYTHIC_PRIVATE_IP|g" "$f"
    sed -i "s|SLIVER_IP_PH|$SLIVER_PRIVATE_IP|g" "$f"
    sed -i "s|HAVOC_IP_PH|$HAVOC_PRIVATE_IP|g" "$f"
    sed -i "s|MYTHIC_PREFIX_PH|$MYTHIC_URI_PREFIX|g" "$f"
    sed -i "s|SLIVER_PREFIX_PH|$SLIVER_URI_PREFIX|g" "$f"
    sed -i "s|HAVOC_PREFIX_PH|$HAVOC_URI_PREFIX|g" "$f"
    sed -i "s|HEADER_NAME_PH|$C2_HEADER_NAME|g" "$f"
    sed -i "s|HEADER_VALUE_PH|$C2_HEADER_VALUE|g" "$f"
done

a2dissite 000-default.conf
a2ensite redirector-http.conf
a2ensite redirector-https.conf

sed -i "s/ServerSignature On/ServerSignature Off/g" /etc/apache2/conf-available/security.conf
sed -i "s/ServerTokens OS/ServerTokens Prod/g" /etc/apache2/conf-available/security.conf

apache2ctl configtest
systemctl restart apache2

# MOTD
cat > /etc/update-motd.d/99-redstack << MOTDEOF
#!/bin/sh
printf '\n+--------------------------------------------------+\n'
printf '|  redStack (Local) | Apache Redirector            |\n'
printf '+--------------------------------------------------+\n\n'
printf '  Local Access: http://localhost:8080\n'
printf '  Local Access: https://localhost:8443\n'
printf '  C2 Header:    $C2_HEADER_NAME: $C2_HEADER_VALUE\n'
printf '\n[Diagnostics]\n'
printf '  Test:        sudo /root/test_redirector.sh\n'
printf '  Access log:  sudo tail -f /var/log/apache2/redirector-access.log\n'
printf '+--------------------------------------------------+\n\n'
MOTDEOF
chmod +x /etc/update-motd.d/99-redstack

echo "===== Redirector Setup Complete (Local VM) $(date) ====="
