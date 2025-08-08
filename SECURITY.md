# Security Implementation

This MCP Server now includes basic security measures to protect against common attacks:

## Features Implemented

### 1. Rate Limiting
- **Protection**: Prevents DDoS attacks by limiting requests per minute
- **Default**: 60 requests per minute per client
- **Configuration**: Set `MAX_REQUESTS_PER_MINUTE` environment variable

### 2. Payload Size Validation  
- **Protection**: Prevents payload bomb attacks
- **Default**: 1MB maximum payload size
- **Configuration**: Set `MAX_PAYLOAD_SIZE` environment variable

### 3. API Client Security
- **Timeout Protection**: 30-second request timeout instead of 100 seconds
- **Response Size Limits**: Maximum 10MB response size
- **Input Validation**: System names limited to 100 characters
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
