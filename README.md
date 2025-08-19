# InsightFinder MCP Server

This project provides a Model Context Protocol (MCP) server that allows Large Language Models (LLMs) to interact with the InsightFinder platform. The server offers comprehensive incident management, anomaly detection, and system monitoring capabilities.

## Features

### Transport Options
- **`stdio` Transport**: Standard I/O communication for MCP clients
- **`http**3. Test from other devices:**
   ```bash
   # Replace with your actual local IP
   curl -k -H "X-API-Key: your-key" \
        -H "X-IF-License-Key: your-license-key" \
        -H "X-IF-User-Name: your-username" \
        https://192.168.1.100/health
   ```nsport**: RESTful HTTP API with authentication and security
- **`https` Transport**: Secure HTTPS with nginx reverse proxy support

### Security Features
- **Multiple Authentication Methods**: API Key, Bearer Token, or Basic Auth
- **Rate Limiting**: Configurable request throttling
- **IP Whitelisting**: Restrict access by IP address or CIDR blocks
- **CORS Support**: Cross-origin resource sharing for web clients
- **Proxy Support**: Full nginx reverse proxy compatibility

## Quick Start

### Prerequisites
- Python 3.8 or higher
- pip package manager
- (Optional) Docker for containerized deployment
- (Optional) nginx for HTTPS deployment

### Installation

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd insightfinder-mcp-server
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -e .
   ```

## Configuration

### Environment Variables

Create a `.env` file in the project root with your server configuration:

```bash
# Transport Configuration
TRANSPORT_TYPE=http  # Options: stdio, http

# HTTP Server Configuration (when using http transport)
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# Authentication Configuration (for HTTP transport)
HTTP_AUTH_ENABLED=true
HTTP_AUTH_METHOD=api_key  # Options: api_key, bearer, basic
HTTP_API_KEY=your-secure-api-key-here

# Security Configuration
HTTP_RATE_LIMIT_ENABLED=true
MAX_REQUESTS_PER_MINUTE=60
HTTP_IP_WHITELIST=192.168.1.0/24  # Optional IP restrictions

# Debug Configuration
ENABLE_DEBUG_MESSAGES=false

# SSE Streaming Configuration
SSE_ENABLED=true
SSE_PING_INTERVAL=30
SSE_MAX_CONNECTIONS=100
SSE_CORS_HEADERS=Cache-Control,Content-Type
SSE_HEARTBEAT_ENABLED=true
```

### InsightFinder Credentials

**Important**: InsightFinder credentials are now provided via HTTP headers on each request, not via environment variables. This allows multiple clients to use different InsightFinder accounts through the same server instance.

Required HTTP headers for all InsightFinder operations:
- `X-IF-License-Key` - Your InsightFinder license key
- `X-IF-User-Name` - Your InsightFinder username

Optional header:
- `X-IF-API-URL` - API endpoint (defaults to https://app.insightfinder.com)

**Benefits of HTTP Header Authentication:**
- **Multi-tenant support**: Multiple clients can use different InsightFinder accounts through the same server
- **Enhanced security**: Credentials are not stored in server configuration or environment variables
- **Flexibility**: Different requests can target different systems or use different credentials
- **Better isolation**: Each request operates with its own credential context

### Configuration Examples

Use the provided example file for different deployment scenarios:
- `.env.example` - Comprehensive server configuration template
- Copy to `.env` and modify based on your needs

**Note**: The `.env` file contains only server configuration. InsightFinder credentials are provided via HTTP headers on each request.

## Running the Server

### Option 1: stdio Transport (for MCP Clients)

**Local Development:**
```bash
# Using the provided script
./scripts/run_server.sh

# Or directly
python -m insightfinder_mcp_server.main
```

**MCP Client Configuration:**
```json
{
  "insightfinder": {
    "command": "python",
    "args": ["-m", "insightfinder_mcp_server.main"],
    "cwd": "/path/to/insightfinder-mcp-server"
  }
}
```

**Note**: When using stdio transport, InsightFinder credentials must be provided by the MCP client through the MCP protocol's initialization or via custom headers if your client supports them.

### Option 2: HTTP Transport with SSE Streaming

**Start HTTP Server with SSE:**
```bash
# Set transport to HTTP with SSE enabled
export TRANSPORT_TYPE=http
export SERVER_HOST=0.0.0.0
export SERVER_PORT=8000
export HTTP_AUTH_ENABLED=true
export HTTP_API_KEY=your-secure-api-key
export SSE_ENABLED=true

python -m insightfinder_mcp_server.main
```

**Test SSE Streaming:**
```bash
# Connect to SSE event stream
curl -H "X-API-Key: your-api-key" \
     -H "Accept: text/event-stream" \
     http://localhost:8000/mcp/events

# Send streaming MCP request with InsightFinder credentials
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-IF-License-Key: your-license-key" \
  -H "X-IF-User-Name: your-username" \
  -H "Accept: text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_incidents","arguments":{"systemName":"test-system"}},"id":1}' \
  http://localhost:8000/mcp/stream

# Stream individual tool execution
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-IF-License-Key: your-license-key" \
  -H "X-IF-User-Name: your-username" \
  -H "Accept: text/event-stream" \
  -d '{"systemName":"test-system"}' \
  http://localhost:8000/tools/list_incidents/stream
```

### Option 3: HTTP Transport (Standard RESTful API)

**Start HTTP Server:**
```bash
# Set transport to HTTP
export TRANSPORT_TYPE=http
export SERVER_HOST=0.0.0.0
export SERVER_PORT=8000
export HTTP_AUTH_ENABLED=true
export HTTP_API_KEY=your-secure-api-key

python -m insightfinder_mcp_server.main
```

**Test HTTP API:**
```bash
# Health check
curl -H "X-API-Key: your-api-key" http://localhost:8000/health

# List available tools
curl -H "X-API-Key: your-api-key" http://localhost:8000/tools

# Execute MCP request with InsightFinder credentials
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-IF-License-Key: your-license-key" \
  -H "X-IF-User-Name: your-username" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
  http://localhost:8000/mcp

# Execute a specific tool
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-IF-License-Key: your-license-key" \
  -H "X-IF-User-Name: your-username" \
  -d '{"systemName":"your-system-name"}' \
  http://localhost:8000/tools/list_incidents
```

### Option 4: HTTPS with nginx (Production)

**Automated Setup:**
```bash
# For production deployment with Let's Encrypt
sudo ./scripts/setup-https.sh your-domain.com 8000

# For local testing with self-signed certificate
sudo ./scripts/setup-local-https.sh
```

**Manual Setup:**
```bash
# 1. Configure nginx with provided template
sudo cp config/nginx/nginx-https.conf /etc/nginx/sites-available/mcp-server
sudo ln -s /etc/nginx/sites-available/mcp-server /etc/nginx/sites-enabled/

# 2. Get SSL certificate
sudo certbot --nginx -d your-domain.com

# 3. Configure for proxy mode
export BEHIND_PROXY=true
export TRUST_PROXY_HEADERS=true
export SERVER_HOST=127.0.0.1

# 4. Start server
python -m insightfinder_mcp_server.main
```

**Test HTTPS Setup:**
```bash
# Use the provided test script
./scripts/test-https.sh your-domain.com your-api-key

# Or manual testing with InsightFinder credentials
curl -H "X-API-Key: your-api-key" \
     -H "X-IF-License-Key: your-license-key" \
     -H "X-IF-User-Name: your-username" \
     https://your-domain.com/health
```

### Option 5: Docker Deployment

**Basic Docker Run (stdio transport):**
```bash
docker run -i --rm \
  -e TRANSPORT_TYPE=stdio \
  docker.io/insightfinder/insightfinder-mcp-server:latest
```

**Docker with HTTP Transport:**
```bash
docker run -d \
  -p 8000:8000 \
  -e TRANSPORT_TYPE=http \
  -e HTTP_AUTH_ENABLED=true \
  -e HTTP_API_KEY=your-secure-api-key \
  docker.io/insightfinder/insightfinder-mcp-server:latest
```

**MCP Client Configuration for Docker:**
```json
{
  "insightfinder": {
    "command": "docker",
    "args": [
      "run", "-i", "--rm",
      "docker.io/insightfinder/insightfinder-mcp-server:latest"
    ],
    "transport": "stdio"
  }
}
```

**Note**: With Docker deployment, InsightFinder credentials are provided via HTTP headers when using HTTP transport, or through the MCP protocol when using stdio transport.

## API Reference

### HTTP Endpoints

When running in HTTP mode, the following endpoints are available:

- `GET /` - Server information and capabilities
- `GET /health` - Health check endpoint
- `GET /tools` - List available tools with schemas
- `POST /tools/{tool_name}` - Execute a specific tool
- `POST /mcp` - Execute MCP JSON-RPC requests
- `POST /mcp/stream` - Streaming MCP requests (deprecated, use SSE endpoints)
- `GET /docs` - Interactive API documentation (Swagger UI)

**SSE Streaming Endpoints (when SSE_ENABLED=true):**
- `GET /mcp/events` - SSE event stream for real-time MCP events
- `POST /mcp/stream` - Streaming MCP requests via Server-Sent Events  
- `POST /tools/{tool_name}/stream` - Stream individual tool execution
- `GET /sse/connections` - Get active SSE connections (debug endpoint)

### InsightFinder HTTP Headers

All InsightFinder tool operations require the following HTTP headers:

**Required Headers:**
- `X-IF-License-Key` - Your InsightFinder license key
- `X-IF-User-Name` - Your InsightFinder username

**Optional Headers:**
- `X-IF-API-URL` - Custom API endpoint (defaults to https://app.insightfinder.com)

**Example Request:**
```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-server-api-key" \
  -H "X-IF-License-Key: your-insightfinder-license-key" \
  -H "X-IF-User-Name: your-insightfinder-username" \
  -d '{"timeRange":"1d","status":"open"}' \
  http://localhost:8000/tools/list_incidents
```

### Authentication Methods

**API Key Authentication:**
```bash
# Header-based (recommended)
curl -H "X-API-Key: your-key" https://api.example.com/health

# Query parameter (fallback)
curl "https://api.example.com/health?api_key=your-key"
```

**Bearer Token Authentication:**
```bash
curl -H "Authorization: Bearer your-token" https://api.example.com/health
```

**Basic Authentication:**
```bash
curl -u username:password https://api.example.com/health
```

## Security Configuration

### Rate Limiting
```bash
HTTP_RATE_LIMIT_ENABLED=true
MAX_REQUESTS_PER_MINUTE=60
```

### IP Whitelisting
```bash
# Single IP
HTTP_IP_WHITELIST=192.168.1.100

# CIDR blocks (comma-separated)
HTTP_IP_WHITELIST=192.168.1.0/24,10.0.0.0/8
```

### CORS Configuration
```bash
HTTP_CORS_ENABLED=true
HTTP_CORS_ORIGINS=https://your-frontend.com,https://localhost:3000
```

## Local Development & Testing

### Home Network Testing

For testing on your local network:

1. **Generate local HTTPS setup:**
   ```bash
   # Creates self-signed certificates and nginx config
   sudo ./scripts/setup-local-https.sh
   ```

2. **Configure for local access:**
   ```bash
   # Update ALLOWED_HOSTS with your local IP
   export ALLOWED_HOSTS=192.168.1.100,localhost,127.0.0.1
   ```

3. **Test from other devices:**
   ```bash
   # Replace with your actual local IP
   curl -k -H "X-API-Key: your-key" https://192.168.1.100/health
   ```

### Development Scripts

- `./scripts/run_server.sh` - Start server in development mode
- `./scripts/setup-https.sh` - Production HTTPS setup with Let's Encrypt  
- `./scripts/setup-local-https.sh` - Local HTTPS setup with self-signed certs
- `./scripts/test-https.sh` - Test HTTPS configuration
- `./scripts/test-local-https.sh` - Test local HTTPS setup
- `./scripts/test-sse.sh` - Test SSE streaming functionality (curl-based)
- `python tests/test_sse.py` - Comprehensive SSE testing (Python-based)

### Helper Scripts for Testing

**Create a test configuration script:**

```bash
#!/bin/bash
# save as test-insightfinder.sh

# Set your credentials
export IF_LICENSE_KEY="your-license-key-here"
export IF_USERNAME="your-username-here"
export API_KEY="your-server-api-key-here"

# Helper function for API calls
call_tool() {
    local tool_name=$1
    local data=$2
    
    curl -X POST \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $API_KEY" \
        -H "X-IF-License-Key: $IF_LICENSE_KEY" \
        -H "X-IF-User-Name: $IF_USERNAME" \
        -d "$data" \
        "http://localhost:8000/tools/$tool_name"
}

# Example usage:
# call_tool "list_incidents" '{"timeRange":"7d"}'
# call_tool "fetch_log_anomalies" '{"startTime":"2024-01-01","endTime":"2024-01-02"}'
```

**Python helper example:**
```python
import requests
import json

class InsightFinderClient:
    def __init__(self, base_url="http://localhost:8000", api_key=None, 
                 license_key=None, username=None):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "X-IF-License-Key": license_key,
            "X-IF-User-Name": username
        }
    
    def call_tool(self, tool_name, **kwargs):
        url = f"{self.base_url}/tools/{tool_name}"
        response = requests.post(url, headers=self.headers, json=kwargs)
        return response.json()
    
    def list_incidents(self, **kwargs):
        return self.call_tool("list_incidents", **kwargs)
    
    def fetch_log_anomalies(self, **kwargs):
        return self.call_tool("fetch_log_anomalies", **kwargs)

# Usage
client = InsightFinderClient(
    api_key="your-server-api-key",
    license_key="your-license-key", 
    username="your-username"
)

incidents = client.list_incidents(timeRange="7d")
```
## Troubleshooting

### Common Issues

**1. InsightFinder Credential Errors:**
```bash
# Missing required headers will return HTTP 400 with error details
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -d '{"systemName":"test"}' \
  http://localhost:8000/tools/list_incidents

# Response: {"error": "Missing required header: X-IF-License-Key"}

# Verify all required headers are included
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "X-IF-License-Key: your-license-key" \
  -H "X-IF-User-Name: your-username" \
  -d '{"systemName":"test"}' \
  http://localhost:8000/tools/list_incidents
```

**2. Authentication Errors:**
```bash
# Verify your server API key is set correctly
echo $HTTP_API_KEY

# Check server logs for authentication details
ENABLE_DEBUG_MESSAGES=true python -m insightfinder_mcp_server.main
```

**3. Proxy/HTTPS Issues:**
```bash
# Ensure proxy settings are configured
export BEHIND_PROXY=true
export TRUST_PROXY_HEADERS=true

# Check nginx error logs
sudo tail -f /var/log/nginx/error.log
```

**4. SSL Certificate Problems:**
```bash
# Test SSL certificate
echo | openssl s_client -servername your-domain.com -connect your-domain.com:443

# Renew Let's Encrypt certificate
sudo certbot renew
```

### Debug Mode

Enable detailed logging for troubleshooting:
```bash
export ENABLE_DEBUG_MESSAGES=true
python -m insightfinder_mcp_server.main
```

## Environment Variables Reference

### Server Configuration Variables
- `TRANSPORT_TYPE` - Transport method (default: stdio)
- `SERVER_HOST` - HTTP server bind address (default: 0.0.0.0)
- `SERVER_PORT` - HTTP server port (default: 8000)
- `ENABLE_DEBUG_MESSAGES` - Enable debug logging (default: false)

### InsightFinder API Configuration
- `INSIGHTFINDER_API_URL` - Default API endpoint (default: https://app.insightfinder.com)

**Note**: Individual InsightFinder credentials (license key, username, system name) are now provided via HTTP headers on each request, not environment variables.

### HTTP Transport Variables
- `HTTP_AUTH_ENABLED` - Enable authentication (default: true)
- `HTTP_AUTH_METHOD` - Auth method: api_key, bearer, basic (default: api_key)
- `HTTP_API_KEY` - API key for authentication
- `HTTP_BEARER_TOKEN` - Bearer token for authentication
- `HTTP_BASIC_USERNAME` - Basic auth username (default: admin)  
- `HTTP_BASIC_PASSWORD` - Basic auth password
- `HTTP_RATE_LIMIT_ENABLED` - Enable rate limiting (default: true)
- `MAX_REQUESTS_PER_MINUTE` - Rate limit threshold (default: 60)
- `HTTP_IP_WHITELIST` - Allowed IP addresses/CIDR blocks
- `HTTP_CORS_ENABLED` - Enable CORS (default: false)
- `HTTP_CORS_ORIGINS` - Allowed CORS origins (default: *)

### SSE Streaming Variables
- `SSE_ENABLED` - Enable Server-Sent Events streaming (default: true)
- `SSE_PING_INTERVAL` - Heartbeat interval in seconds (default: 30)
- `SSE_MAX_CONNECTIONS` - Maximum concurrent SSE connections (default: 100)
- `SSE_CORS_HEADERS` - Additional CORS headers for SSE (default: Cache-Control,Content-Type)
- `SSE_HEARTBEAT_ENABLED` - Enable heartbeat events (default: true)

### Proxy Variables
- `BEHIND_PROXY` - Server is behind reverse proxy (default: false)
- `TRUST_PROXY_HEADERS` - Trust proxy forwarded headers (default: false)  
- `ALLOWED_HOSTS` - Comma-separated list of allowed hostnames

## Contributing

### Project Structure
```
├── src/
│   └── insightfinder_mcp_server/        # Main application code
│       ├── __init__.py
│       ├── main.py                      # Application entry point
│       ├── api_client/                  # InsightFinder API client
│       ├── config/                      # Configuration management
│       ├── security/                    # Authentication & security
│       └── server/                      # MCP server implementation
├── scripts/                             # Deployment & utility scripts
│   ├── run_server.sh                    # Development server launcher
│   ├── setup-https.sh                   # Production HTTPS setup
│   ├── setup-local-https.sh             # Local HTTPS setup  
│   ├── test-https.sh                    # HTTPS testing
│   └── test-local-https.sh              # Local HTTPS testing
├── config/
│   └── nginx/                           # Nginx configuration templates
│       └── nginx-https.conf             # HTTPS proxy configuration
├── tests/                               # Test suites
├── docs/                                # Documentation
├── .env.example                         # Environment configuration template
├── Dockerfile                           # Container configuration
├── pyproject.toml                       # Python project configuration
└── README.md                            # This file
```

## License

[License information here]