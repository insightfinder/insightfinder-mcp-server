#!/bin/bash
# Test script for local HTTPS setup

set -e

# Get local network information
LOCAL_IP=$(hostname -I | awk '{print $1}')
HOSTNAME=$(hostname)

# Get API key from environment file
ENV_FILE="$(dirname "$0")/../.env.local"
if [ -f "$ENV_FILE" ]; then
    API_KEY=$(grep HTTP_API_KEY "$ENV_FILE" 2>/dev/null | cut -d'=' -f2 || echo "test-key")
else
    API_KEY="test-key"
    echo "Warning: $ENV_FILE not found, using default API key"
fi

echo "Testing Local HTTPS MCP Server"
echo "=============================="
echo "Local IP: $LOCAL_IP"
echo "Hostname: $HOSTNAME" 
echo "API Key: $API_KEY"
echo ""

# Test endpoints
ENDPOINTS=(
    "https://localhost"
    "https://$LOCAL_IP" 
    "https://127.0.0.1"
)

for endpoint in "${ENDPOINTS[@]}"; do
    echo "Testing $endpoint..."
    
    # Test health endpoint
    echo -n "  Health check: "
    if curl -k -s -H "X-API-Key: $API_KEY" "$endpoint/health" > /dev/null 2>&1; then
        echo "✅ OK"
    else
        echo "❌ FAILED"
        continue
    fi
    
    # Test root endpoint
    echo -n "  Root endpoint: "
    if curl -k -s -H "X-API-Key: $API_KEY" "$endpoint/" > /dev/null 2>&1; then
        echo "✅ OK"
    else
        echo "❌ FAILED"
    fi
    
    # Test tools endpoint
    echo -n "  Tools endpoint: "
    if curl -k -s -H "X-API-Key: $API_KEY" "$endpoint/tools" > /dev/null 2>&1; then
        echo "✅ OK"
    else
        echo "❌ FAILED"
    fi
    
    # Test MCP endpoint
    echo -n "  MCP endpoint: "
    if curl -k -s -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
        "$endpoint/mcp" > /dev/null 2>&1; then
        echo "✅ OK"
    else
        echo "❌ FAILED"
    fi
    
    echo ""
done

# Test SSL certificate
echo "Testing SSL certificate..."
echo -n "  Certificate validity: "
if echo | openssl s_client -servername localhost -connect localhost:443 2>/dev/null | \
   openssl x509 -noout -dates 2>/dev/null > /dev/null; then
    echo "✅ OK"
    echo "  Certificate details:"
    echo | openssl s_client -servername localhost -connect localhost:443 2>/dev/null | \
    openssl x509 -noout -subject -dates 2>/dev/null | sed 's/^/    /'
else
    echo "❌ FAILED"
fi

echo ""
echo "Testing from other devices on your network:"
echo "  curl -k -H 'X-API-Key: $API_KEY' https://$LOCAL_IP/health"
echo ""
echo "Browser testing:"
echo "  Open https://$LOCAL_IP in your browser (accept security warning)"
echo "  Add header: X-API-Key: $API_KEY"
echo ""
echo "Mobile testing:"
echo "  1. Connect your phone to the same WiFi network"
echo "  2. Open https://$LOCAL_IP in mobile browser"  
echo "  3. Accept the security certificate warning"
echo "  4. You should see the server information page"
echo ""
echo "Note: Use -k flag with curl to ignore self-signed certificate warnings"
