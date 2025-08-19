# Security Implementation

This MCP Server includes comprehensive security measures to protect against attacks and unauthorized access.

## HTTP Server Authentication & Authorization

### Authentication Methods

#### 1. API Key Authentication (Recommended)
```bash
export HTTP_AUTH_ENABLED=true
export HTTP_AUTH_METHOD=api_key
export HTTP_API_KEY=your-secure-api-key-here
```

**Client Usage:**
```bash
curl -H "X-API-Key: your-secure-api-key-here" http://localhost:8000/mcp
```

#### 2. Bearer Token Authentication
```bash
export HTTP_AUTH_ENABLED=true
export HTTP_AUTH_METHOD=bearer
export HTTP_BEARER_TOKEN=your-secure-bearer-token-here
```

**Client Usage:**
```bash
curl -H "Authorization: Bearer your-secure-bearer-token-here" http://localhost:8000/mcp
```

#### 3. Basic Authentication
```bash
export HTTP_AUTH_ENABLED=true
export HTTP_AUTH_METHOD=basic
export HTTP_BASIC_USERNAME=admin
export HTTP_BASIC_PASSWORD=your-secure-password
```

**Client Usage:**
```bash
curl -u "admin:your-secure-password" http://localhost:8000/mcp
```

### Additional Security Features

#### IP Whitelisting
Restrict access to specific IP addresses or networks:
```bash
export HTTP_IP_WHITELIST="192.168.1.0/24,10.0.0.100"
```

#### CORS Configuration
For web browser access:
```bash
export HTTP_CORS_ENABLED=true
export HTTP_CORS_ORIGINS="https://yourdomain.com,https://anotherdomain.com"
```

## Security Features Implemented

## Security Features Implemented

### 1. Authentication & Authorization
- **HTTP Authentication**: API Key, Bearer Token, or Basic Auth
- **IP Whitelisting**: Restrict access to specific networks/IPs
- **Automatic Credential Generation**: Secure defaults if not provided
- **Multiple Auth Methods**: Flexible authentication options

### 2. Rate Limiting
- **Protection**: Prevents DDoS attacks by limiting requests per minute
- **Default**: 60 requests per minute per client
- **Per-IP Tracking**: Separate limits for each client IP
- **Configuration**: Set `MAX_REQUESTS_PER_MINUTE` environment variable
- **Blocking**: Temporary blocks for rate limit violations

### 3. Request Validation & Size Limits  
- **Protection**: Prevents payload bomb attacks
- **Default**: 1MB maximum payload size
- **HTTP Method Restrictions**: Only GET/POST allowed on protected endpoints
- **Configuration**: Set `MAX_PAYLOAD_SIZE` environment variable

### 4. CORS & Security Headers
- **CORS Control**: Configurable cross-origin access
- **Security Headers**: Automatic security header management
- **Content-Type Validation**: Strict content-type checking

### 5. API Client Security
- **Timeout Protection**: 30-second request timeout instead of 100 seconds
- **Response Size Limits**: Maximum 10MB response size
- **Input Validation**: System names limited to 100 characters
- **Authentication Headers**: Automatic auth header management

### 6. Monitoring & Logging
- **Security Event Logging**: Authentication failures, rate limits, IP blocks
- **Debug Mode**: Detailed security logging when enabled
- **Error Handling**: Secure error responses without information disclosure
- **Time Range Limits**: Maximum 1 year time range queries
- **Result Limits**: Maximum 5000 items per response

### 4. String Length Controls
- **Automatic Truncation**: Long strings are truncated with clear indication
- **Raw Data Limits**: Raw data requests limited to 10KB maximum

## Environment Variables

```bash
# Security settings (optional)
MAX_REQUESTS_PER_MINUTE=60        # Rate limit (default: 60)
MAX_PAYLOAD_SIZE=1048576          # Max payload size in bytes (default: 1MB)
```

## Security Measures Applied

1. **All tool functions** now check rate limits before processing
2. **Input validation** on system names and parameters  
3. **Payload size checking** to prevent memory exhaustion
4. **String truncation** to prevent overwhelming responses
5. **Timeout protection** to prevent hung requests
6. **Error message sanitization** to prevent information leakage

## Customization

To adjust security settings, modify the environment variables:

```bash
# Stricter rate limiting
export MAX_REQUESTS_PER_MINUTE=30

# Smaller payload limit  
export MAX_PAYLOAD_SIZE=512000    # 512KB

# More permissive (not recommended for production)
export MAX_REQUESTS_PER_MINUTE=120
```

The security implementation is lightweight and focuses on the most critical vulnerabilities while maintaining performance.
