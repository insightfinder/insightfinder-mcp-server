import os
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

class Settings:
    """
    Application settings loaded from environment variables.
    """
    # InsightFinder API Configuration (will be provided via HTTP headers)
    INSIGHTFINDER_API_URL: str = "https://app.insightfinder.com"
    # INSIGHTFINDER_LICENSE_KEY: str = os.getenv("INSIGHTFINDER_LICENSE_KEY")
    # INSIGHTFINDER_SYSTEM_NAME: str = os.getenv("INSIGHTFINDER_SYSTEM_NAME")  # Not required for now
    # INSIGHTFINDER_USER_NAME: str = os.getenv("INSIGHTFINDER_USER_NAME")

    # MCP Server Configuration
    SERVER_NAME: str = "InsightFinderMCPServer"
    SERVER_VERSION: str = "1.2.0"
    
    # HTTP Server Configuration
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
    
    # Proxy Configuration
    BEHIND_PROXY: bool = os.getenv("BEHIND_PROXY", "false").lower() == "true"
    TRUST_PROXY_HEADERS: bool = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    ALLOWED_HOSTS: str = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1")
    
    # Transport Configuration
    TRANSPORT_TYPE: str = os.getenv("TRANSPORT_TYPE", "http")  # stdio, http
    
    # Logging Configuration
    ENABLE_DEBUG_MESSAGES: bool = os.getenv("ENABLE_DEBUG_MESSAGES", "false").lower() == "true"
    
    # Basic Security Configuration
    MAX_REQUESTS_PER_MINUTE: int = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))
    MAX_PAYLOAD_SIZE: int = int(os.getenv("MAX_PAYLOAD_SIZE", str(1024 * 1024)))  # 1MB default

    # Authentication Configuration
    HTTP_AUTH_ENABLED: bool = os.getenv("HTTP_AUTH_ENABLED", "true").lower() == "true"
    HTTP_AUTH_METHOD: str = os.getenv("HTTP_AUTH_METHOD", "api_key")  # api_key, bearer, basic
    HTTP_API_KEY: str = os.getenv("HTTP_API_KEY", "")
    HTTP_BEARER_TOKEN: str = os.getenv("HTTP_BEARER_TOKEN", "")
    HTTP_BASIC_USERNAME: str = os.getenv("HTTP_BASIC_USERNAME", "admin")
    HTTP_BASIC_PASSWORD: str = os.getenv("HTTP_BASIC_PASSWORD", "")
    
    # CORS Configuration
    HTTP_CORS_ENABLED: bool = os.getenv("HTTP_CORS_ENABLED", "false").lower() == "true"
    HTTP_CORS_ORIGINS: str = os.getenv("HTTP_CORS_ORIGINS", "*")
    
    # IP Whitelist Configuration
    HTTP_IP_WHITELIST: str = os.getenv("HTTP_IP_WHITELIST", "")  # Comma-separated IPs
    
    # Rate Limiting Configuration
    HTTP_RATE_LIMIT_ENABLED: bool = os.getenv("HTTP_RATE_LIMIT_ENABLED", "true").lower() == "true"
    
    # SSE Configuration
    SSE_ENABLED: bool = os.getenv("SSE_ENABLED", "true").lower() == "true"
    SSE_PING_INTERVAL: int = int(os.getenv("SSE_PING_INTERVAL", "30"))
    SSE_MAX_CONNECTIONS: int = int(os.getenv("SSE_MAX_CONNECTIONS", "100"))
    SSE_CORS_HEADERS: str = os.getenv("SSE_CORS_HEADERS", "Cache-Control,Content-Type")
    SSE_HEARTBEAT_ENABLED: bool = os.getenv("SSE_HEARTBEAT_ENABLED", "true").lower() == "true"

# Create a singleton instance of the settings
settings = Settings()

# Note: InsightFinder credentials will be provided via HTTP headers
# No validation needed for environment variables as they come from client requests