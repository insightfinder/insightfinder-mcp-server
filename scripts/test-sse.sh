#!/bin/bash
# InsightFinder MCP Server SSE Testing Script
# Simple curl-based testing for SSE endpoints

set -e

# Configuration
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-XzbKpR44CNQ3zdyJSrKP5GbrVI5RO2A3w4F44UhUKMA}"

echo "🚀 InsightFinder MCP Server SSE Test"
echo "====================================="
echo "Server URL: $BASE_URL"
echo "API Key: ${API_KEY}..."
echo

# Helper function for API calls
call_api() {
    local method="$1"
    local endpoint="$2"
    local data="$3"
    local content_type="${4:-application/json}"
    local accept="${5:-application/json}"
    
    if [[ "$method" == "GET" ]]; then
        curl -s -H "X-API-Key: $API_KEY" -H "Accept: $accept" "$BASE_URL$endpoint"
    else
        curl -s -X "$method" \
             -H "X-API-Key: $API_KEY" \
             -H "Content-Type: $content_type" \
             -H "Accept: $accept" \
             -d "$data" \
             "$BASE_URL$endpoint"
    fi
}

# Test 1: Server capabilities
echo "🔍 Test 1: Server capabilities"
echo "------------------------------"
capabilities=$(call_api "GET" "/" "")
echo "$capabilities" | python3 -m json.tool 2>/dev/null || echo "$capabilities"
echo

# Check if SSE is enabled
sse_enabled=$(echo "$capabilities" | grep -o '"streaming"[^}]*"supported"[^,]*true' || echo "")
if [[ -n "$sse_enabled" ]]; then
    echo "✅ SSE Streaming is enabled"
else
    echo "⚠️  SSE Streaming may not be enabled"
fi
echo

# Test 2: List tools
echo "📋 Test 2: List available tools"
echo "-------------------------------"
tools=$(call_api "GET" "/tools" "")
echo "$tools" | python3 -m json.tool 2>/dev/null || echo "$tools"
echo

# Test 3: Health check
echo "🏥 Test 3: Health check"
echo "-----------------------"
health=$(call_api "GET" "/health" "")
echo "$health" | python3 -m json.tool 2>/dev/null || echo "$health"
echo

# Test 4: SSE connections info
echo "📊 Test 4: SSE connections info"
echo "-------------------------------"
connections=$(call_api "GET" "/sse/connections" "")
echo "$connections" | python3 -m json.tool 2>/dev/null || echo "$connections"
echo

# Test 5: SSE event stream (with timeout)
echo "🔌 Test 5: SSE event stream (10 seconds)"
echo "----------------------------------------"
echo "Connecting to SSE stream..."
timeout 10s curl -N -H "X-API-Key: $API_KEY" -H "Accept: text/event-stream" "$BASE_URL/mcp/events" 2>/dev/null || echo "✅ SSE connection test completed"
echo

# Test 6: Streaming MCP request
echo "🔧 Test 6: Streaming MCP tools/list request"
echo "-------------------------------------------"
mcp_request='{"jsonrpc":"2.0","method":"tools/list","id":1}'
echo "Sending MCP request: $mcp_request"
echo "Response:"
timeout 10s curl -X POST \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -d "$mcp_request" \
    "$BASE_URL/mcp/stream" 2>/dev/null || echo "✅ Streaming MCP request completed"
echo

# Test 7: Tool streaming (if list_incidents tool is available)
echo "🔧 Test 7: Tool streaming (list_incidents)"
echo "------------------------------------------"
tool_args='{"systemName":"test-system"}'
echo "Streaming tool with args: $tool_args"
echo "Response:"
timeout 10s curl -X POST \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" \
    -d "$tool_args" \
    "$BASE_URL/tools/list_incidents/stream" 2>/dev/null || echo "✅ Tool streaming test completed"
echo

# # Test 8: Standard MCP request (non-streaming)
# echo "📤 Test 8: Standard MCP request (non-streaming)"
# echo "-----------------------------------------------"
# mcp_request='{"jsonrpc":"2.0","method":"tools/list","id":2}'
# echo "Sending standard MCP request: $mcp_request"
# response=$(call_api "POST" "/mcp" "$mcp_request")
# echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"
# echo

# echo "🎉 All tests completed!"
# echo "======================"
# echo
# echo "💡 Tips:"
# echo "- Use 'python tests/test_sse.py' for more detailed testing"
# echo "- Check server logs for any error messages"
# echo "- Ensure SSE_ENABLED=true in your environment"
# echo
# echo "📚 SSE Endpoints:"
# echo "- GET  /mcp/events              - SSE event stream"
# echo "- POST /mcp/stream              - Streaming MCP requests"
# echo "- POST /tools/{name}/stream     - Stream individual tools"
# echo "- GET  /sse/connections         - Connection debug info"
