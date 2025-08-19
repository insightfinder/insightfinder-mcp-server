#!/bin/bash
# Local HTTPS setup script for InsightFinder MCP Server (home network testing)

set -e

echo "InsightFinder MCP Server Local HTTPS Setup"
echo "=========================================="

# Get local network information
LOCAL_IP=$(hostname -I | awk '{print $1}')
HOSTNAME=$(hostname)
MCP_PORT=${1:-"8000"}

echo "Setting up local HTTPS for MCP Server..."
echo "Local IP: $LOCAL_IP"
echo "Hostname: $HOSTNAME"
echo "MCP Port: $MCP_PORT"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "This script needs to be run with sudo privileges for nginx configuration"
    echo "Run: sudo $0 [port]"
    exit 1
fi

# Install nginx if not installed
if ! command -v nginx &> /dev/null; then
    echo "Installing nginx..."
    apt update
    apt install -y nginx openssl
fi

# Create SSL directory
echo "Creating SSL directory..."
mkdir -p /etc/nginx/ssl

# Generate self-signed certificate with multiple SANs
echo "Generating self-signed certificate..."
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/mcp-server-local.key \
    -out /etc/nginx/ssl/mcp-server-local.crt \
    -subj "/C=US/ST=Local/L=Home/O=Testing/CN=$LOCAL_IP" \
    -addext "subjectAltName=DNS:localhost,DNS:$HOSTNAME,IP:$LOCAL_IP,IP:127.0.0.1"

# Set proper permissions
chmod 600 /etc/nginx/ssl/mcp-server-local.key
chmod 644 /etc/nginx/ssl/mcp-server-local.crt

# Create nginx configuration for local testing
echo "Creating nginx configuration..."
cat > /etc/nginx/sites-available/mcp-server-local << EOF
# HTTP redirect to HTTPS
server {
    listen 80;
    server_name localhost $LOCAL_IP $HOSTNAME;
    return 301 https://\$server_name\$request_uri;
}

# HTTPS server for local testing
server {
    listen 443 ssl http2;
    server_name localhost $LOCAL_IP $HOSTNAME;
    
    # SSL certificate paths
    ssl_certificate /etc/nginx/ssl/mcp-server-local.crt;
    ssl_certificate_key /etc/nginx/ssl/mcp-server-local.key;
    
    # Basic SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Security headers (relaxed for local testing)
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options SAMEORIGIN;
    
    # Proxy configuration
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Forwarded-Port \$server_port;
    
    # Timeout settings
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
    
    # Main proxy location
    location / {
        proxy_pass http://127.0.0.1:$MCP_PORT;
    }
    
    # Streaming endpoints
    location /mcp/stream {
        proxy_pass http://127.0.0.1:$MCP_PORT;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }
    
    # Health check endpoint
    location /health {
        proxy_pass http://127.0.0.1:$MCP_PORT;
        access_log off;
    }
    
    # API documentation
    location ~ ^/(docs|openapi\.json|redoc)$ {
        proxy_pass http://127.0.0.1:$MCP_PORT;
    }
    
    # Logging
    access_log /var/log/nginx/mcp-server-local-access.log;
    error_log /var/log/nginx/mcp-server-local-error.log;
}
EOF

# Enable the site
echo "Enabling nginx site..."
ln -sf /etc/nginx/sites-available/mcp-server-local /etc/nginx/sites-enabled/

# Remove default nginx site if it exists
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    rm -f /etc/nginx/sites-enabled/default
fi

# Test nginx configuration
echo "Testing nginx configuration..."
nginx -t

# Restart nginx
echo "Restarting nginx..."
systemctl restart nginx
systemctl enable nginx

# Create local environment file if it doesn't exist
ENV_FILE="$(dirname "$0")/../.env.local"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating local environment file..."
    API_KEY="local-test-$(openssl rand -hex 8)"
    cat > "$ENV_FILE" << EOF
# Local Testing Configuration for InsightFinder MCP Server
TRANSPORT_TYPE=http
SERVER_HOST=127.0.0.1
SERVER_PORT=$MCP_PORT

# Local proxy settings
BEHIND_PROXY=true
TRUST_PROXY_HEADERS=true
ALLOWED_HOSTS=$LOCAL_IP,localhost,127.0.0.1,$HOSTNAME

# Authentication
HTTP_AUTH_ENABLED=true
HTTP_AUTH_METHOD=api_key
HTTP_API_KEY=$API_KEY

# Relaxed security for local testing
HTTP_RATE_LIMIT_ENABLED=true
MAX_REQUESTS_PER_MINUTE=100
MAX_PAYLOAD_SIZE=1048576

# CORS enabled for local testing
HTTP_CORS_ENABLED=true
HTTP_CORS_ORIGINS=https://$LOCAL_IP,https://localhost,https://$HOSTNAME

# InsightFinder API (update these)
INSIGHTFINDER_API_URL=https://app.insightfinder.com
INSIGHTFINDER_LICENSE_KEY=your-license-key-here
INSIGHTFINDER_SYSTEM_NAME=test-system
INSIGHTFINDER_USER_NAME=your-username-here

# Enable debug for testing
ENABLE_DEBUG_MESSAGES=true
EOF
    echo "Created $ENV_FILE with API key: $API_KEY"
fi

echo ""
echo "✅ Local HTTPS setup complete!"
echo ""
echo "Your MCP server can now be accessed at:"
echo "  https://localhost"
echo "  https://$LOCAL_IP"
echo "  https://$HOSTNAME"
echo ""
echo "Configuration:"
echo "  Environment file: $ENV_FILE"
echo "  SSL Certificate: /etc/nginx/ssl/mcp-server-local.crt"
echo "  SSL Key: /etc/nginx/ssl/mcp-server-local.key"
echo ""
echo "Next steps:"
echo "1. Update your InsightFinder credentials in: $ENV_FILE"
echo "2. Start your MCP server: python -m insightfinder_mcp_server.main"
echo "3. Test from this machine: curl -k -H \"X-API-Key: \$(grep HTTP_API_KEY $ENV_FILE | cut -d'=' -f2)\" https://localhost/health"
echo "4. Test from other devices: curl -k -H \"X-API-Key: \$(grep HTTP_API_KEY $ENV_FILE | cut -d'=' -f2)\" https://$LOCAL_IP/health"
echo ""
echo "Note: Use -k flag with curl to ignore self-signed certificate warnings"
echo "Nginx logs: /var/log/nginx/mcp-server-local-*.log"
