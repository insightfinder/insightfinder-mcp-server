#!/bin/bash
# Test script for HTTPS MCP Server

DOMAIN=${1:-"localhost"}
API_KEY=${2:-"test-api-key"}

echo "Testing HTTPS MCP Server"
echo "========================"
echo "Domain: $DOMAIN"
echo "API Key: $API_KEY"
echo ""

# Test health endpoint
echo "1. Testing health endpoint..."
curl -k -s -H "X-API-Key: $API_KEY" "https://$DOMAIN/health" | jq . || echo "Health check failed"
echo ""

# Test root endpoint
echo "2. Testing root endpoint..."
curl -k -s -H "X-API-Key: $API_KEY" "https://$DOMAIN/" | jq . || echo "Root endpoint failed"
echo ""

# Test tools list
echo "3. Testing tools list..."
curl -k -s -H "X-API-Key: $API_KEY" "https://$DOMAIN/tools" | jq . || echo "Tools list failed"
echo ""

# Test MCP endpoint with tools/list
echo "4. Testing MCP tools/list..."
curl -k -s -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
     "https://$DOMAIN/mcp" | jq . || echo "MCP tools/list failed"
echo ""

# Test without API key (should fail)
echo "5. Testing without API key (should fail)..."
curl -k -s "https://$DOMAIN/health" | jq . || echo "Expected failure - no API key"
echo ""

# Test SSL certificate
echo "6. Testing SSL certificate..."
echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null | openssl x509 -noout -dates || echo "SSL certificate test failed"
echo ""

echo "Test complete!"
