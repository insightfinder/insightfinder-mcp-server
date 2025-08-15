#!/bin/bash
# Setup script for HTTPS deployment with nginx

set -e

echo "InsightFinder MCP Server HTTPS Setup"
echo "====================================="

# Configuration variables
DOMAIN=${1:-"your-domain.com"}
MCP_PORT=${2:-"8000"}
NGINX_CONF_NAME="mcp-server"

if [ "$DOMAIN" = "your-domain.com" ]; then
    echo "Usage: $0 <your-domain.com> [port]"
    echo "Example: $0 api.example.com 8000"
    exit 1
fi

echo "Setting up HTTPS for domain: $DOMAIN"
echo "MCP Server port: $MCP_PORT"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "This script needs to be run with sudo privileges for nginx configuration"
    echo "Run: sudo $0 $DOMAIN $MCP_PORT"
    exit 1
fi

# Install required packages
echo "Installing required packages..."
apt update
apt install -y nginx certbot python3-certbot-nginx

# Stop nginx temporarily
systemctl stop nginx

# Create nginx configuration
echo "Creating nginx configuration..."
NGINX_CONF="/etc/nginx/sites-available/$NGINX_CONF_NAME"

cat > "$NGINX_CONF" << EOF
# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

# Main HTTPS server configuration
server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    
    # SSL Configuration (will be configured by certbot)
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    
    # Modern SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy "strict-origin-when-cross-origin";
    
    server_tokens off;
    client_max_body_size 1M;
    
    # Proxy settings
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-Port \$server_port;
    
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
    
    proxy_buffering on;
    proxy_buffer_size 128k;
    proxy_buffers 4 256k;
    proxy_busy_buffers_size 256k;
    
    location / {
        proxy_pass http://127.0.0.1:$MCP_PORT;
    }
    
    location /mcp/stream {
        proxy_pass http://127.0.0.1:$MCP_PORT;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:$MCP_PORT;
        access_log off;
    }
    
    access_log /var/log/nginx/mcp-server-access.log;
    error_log /var/log/nginx/mcp-server-error.log;
}
EOF

# Enable the site
echo "Enabling nginx site..."
ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/$NGINX_CONF_NAME"

# Remove default nginx site if it exists
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    rm -f "/etc/nginx/sites-enabled/default"
fi

# Test nginx configuration
echo "Testing nginx configuration..."
nginx -t

# Start nginx
echo "Starting nginx..."
systemctl start nginx
systemctl enable nginx

# Get SSL certificate using Let's Encrypt
echo "Getting SSL certificate from Let's Encrypt..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@"$DOMAIN"

# Setup auto-renewal
echo "Setting up certificate auto-renewal..."
(crontab -l 2>/dev/null; echo "0 12 * * * /usr/bin/certbot renew --quiet") | crontab -

# Create environment file template
ENV_FILE="$(dirname "$0")/.env.https"
cat > "$ENV_FILE" << EOF
# HTTPS Configuration for InsightFinder MCP Server
TRANSPORT_TYPE=http
SERVER_HOST=127.0.0.1
SERVER_PORT=$MCP_PORT

# Proxy settings
BEHIND_PROXY=true
TRUST_PROXY_HEADERS=true
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1

# Authentication (generate secure values)
HTTP_AUTH_ENABLED=true
HTTP_AUTH_METHOD=api_key
HTTP_API_KEY=\$(openssl rand -base64 32)

# Rate limiting
HTTP_RATE_LIMIT_ENABLED=true
MAX_REQUESTS_PER_MINUTE=60

# CORS (if needed)
HTTP_CORS_ENABLED=false
HTTP_CORS_ORIGINS=https://$DOMAIN

# InsightFinder API (fill these in)
INSIGHTFINDER_LICENSE_KEY=your-license-key
INSIGHTFINDER_SYSTEM_NAME=your-system-name
INSIGHTFINDER_USER_NAME=your-username
EOF

echo ""
echo "✅ HTTPS setup complete!"
echo ""
echo "Next steps:"
echo "1. Update your environment variables in: $ENV_FILE"
echo "2. Start your MCP server with: TRANSPORT_TYPE=http SERVER_HOST=127.0.0.1 SERVER_PORT=$MCP_PORT BEHIND_PROXY=true TRUST_PROXY_HEADERS=true python -m insightfinder_mcp_server.main"
echo "3. Test your setup:"
echo "   curl -k -H \"X-API-Key: your-api-key\" https://$DOMAIN/health"
echo ""
echo "Your MCP server will be available at: https://$DOMAIN"
echo "Nginx logs: /var/log/nginx/mcp-server-*.log"
echo ""
