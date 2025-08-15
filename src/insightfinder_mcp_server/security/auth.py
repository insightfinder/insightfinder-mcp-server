import secrets
import hashlib
import base64
import time
from typing import Optional, Dict, Any, List
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPBasic, HTTPBasicCredentials, APIKeyHeader
from fastapi.security.utils import get_authorization_scheme_param
import ipaddress
import logging

from ..config.settings import settings

logger = logging.getLogger(__name__)

class AuthenticationError(HTTPException):
    """Custom authentication error exception."""
    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

class AuthorizationError(HTTPException):
    """Custom authorization error exception."""
    def __init__(self, detail: str = "Access denied"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

class RateLimitError(HTTPException):
    """Custom rate limit error exception."""
    def __init__(self, detail: str = "Rate limit exceeded"):
        super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)

class SecurityManager:
    """Manages authentication, authorization, and rate limiting for the HTTP server."""
    
    def __init__(self):
        self.api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        self.bearer_security = HTTPBearer(auto_error=False)
        self.basic_security = HTTPBasic(auto_error=False)
        
        # Rate limiting storage (in production, use Redis or similar)
        self._rate_limit_storage: Dict[str, Dict[str, Any]] = {}
        
        # Parse IP whitelist
        self.ip_whitelist = self._parse_ip_whitelist()
        
        # Generate default credentials if not provided
        self._ensure_auth_credentials()
    
    def _parse_ip_whitelist(self) -> Optional[List[ipaddress.IPv4Network]]:
        """Parse IP whitelist from settings."""
        if not settings.HTTP_IP_WHITELIST:
            return None
        
        whitelist = []
        for ip_str in settings.HTTP_IP_WHITELIST.split(","):
            ip_str = ip_str.strip()
            if ip_str:
                try:
                    # Support both single IPs and CIDR notation
                    if "/" not in ip_str:
                        ip_str += "/32"
                    whitelist.append(ipaddress.IPv4Network(ip_str, strict=False))
                except ValueError as e:
                    logger.warning(f"Invalid IP in whitelist: {ip_str} - {e}")
        
        return whitelist if whitelist else None
    
    def _ensure_auth_credentials(self):
        """Generate default credentials if not provided."""
        if settings.HTTP_AUTH_ENABLED:
            if settings.HTTP_AUTH_METHOD == "api_key" and not settings.HTTP_API_KEY:
                # Generate a secure API key
                api_key = secrets.token_urlsafe(32)
                logger.warning(f"No API key provided. Generated secure API key: {api_key}")
                logger.warning("Set HTTP_API_KEY environment variable to use a custom API key")
                settings.HTTP_API_KEY = api_key
            
            elif settings.HTTP_AUTH_METHOD == "bearer" and not settings.HTTP_BEARER_TOKEN:
                # Generate a secure bearer token
                bearer_token = secrets.token_urlsafe(32)
                logger.warning(f"No bearer token provided. Generated secure bearer token: {bearer_token}")
                logger.warning("Set HTTP_BEARER_TOKEN environment variable to use a custom bearer token")
                settings.HTTP_BEARER_TOKEN = bearer_token
            
            elif settings.HTTP_AUTH_METHOD == "basic" and not settings.HTTP_BASIC_PASSWORD:
                # Generate a secure password
                password = secrets.token_urlsafe(16)
                logger.warning(f"No basic auth password provided. Generated secure password: {password}")
                logger.warning("Set HTTP_BASIC_PASSWORD environment variable to use a custom password")
                settings.HTTP_BASIC_PASSWORD = password
    
    def check_ip_whitelist(self, client_ip: str) -> bool:
        """Check if client IP is in whitelist."""
        if not self.ip_whitelist:
            return True
        
        try:
            client_addr = ipaddress.IPv4Address(client_ip)
            for network in self.ip_whitelist:
                if client_addr in network:
                    return True
            return False
        except ValueError:
            logger.warning(f"Invalid client IP address: {client_ip}")
            return False
    
    def check_rate_limit(self, client_id: str) -> bool:
        """Check if client has exceeded rate limit."""
        if not settings.HTTP_RATE_LIMIT_ENABLED:
            return True
        
        now = time.time()
        window_start = now - 60  # 1-minute window
        
        if client_id not in self._rate_limit_storage:
            self._rate_limit_storage[client_id] = {"requests": [], "blocked_until": 0}
        
        client_data = self._rate_limit_storage[client_id]
        
        # Check if client is currently blocked
        if now < client_data["blocked_until"]:
            return False
        
        # Clean old requests outside the window
        client_data["requests"] = [req_time for req_time in client_data["requests"] if req_time > window_start]
        
        # Check rate limit
        if len(client_data["requests"]) >= settings.MAX_REQUESTS_PER_MINUTE:
            # Block for 1 minute
            client_data["blocked_until"] = now + 60
            logger.warning(f"Rate limit exceeded for client {client_id}")
            return False
        
        # Add current request
        client_data["requests"].append(now)
        return True
    
    async def authenticate_api_key(self, request: Request) -> bool:
        """Authenticate using API key."""
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            # Also check query parameter as fallback
            api_key = request.query_params.get("api_key")
        
        if not api_key:
            raise AuthenticationError("Missing API key")
        
        if api_key != settings.HTTP_API_KEY:
            raise AuthenticationError("Invalid API key")
        
        return True
    
    async def authenticate_bearer(self, request: Request) -> bool:
        """Authenticate using Bearer token."""
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise AuthenticationError("Missing Authorization header")
        
        scheme, token = get_authorization_scheme_param(authorization)
        if scheme.lower() != "bearer":
            raise AuthenticationError("Invalid authentication scheme")
        
        if not token:
            raise AuthenticationError("Missing bearer token")
        
        if token != settings.HTTP_BEARER_TOKEN:
            raise AuthenticationError("Invalid bearer token")
        
        return True
    
    async def authenticate_basic(self, request: Request) -> bool:
        """Authenticate using Basic auth."""
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise AuthenticationError("Missing Authorization header")
        
        scheme, credentials = get_authorization_scheme_param(authorization)
        if scheme.lower() != "basic":
            raise AuthenticationError("Invalid authentication scheme")
        
        if not credentials:
            raise AuthenticationError("Missing credentials")
        
        try:
            decoded = base64.b64decode(credentials).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            raise AuthenticationError("Invalid credentials format")
        
        if username != settings.HTTP_BASIC_USERNAME or password != settings.HTTP_BASIC_PASSWORD:
            raise AuthenticationError("Invalid username or password")
        
        return True
    
    async def authenticate(self, request: Request) -> bool:
        """Main authentication method."""
        if not settings.HTTP_AUTH_ENABLED:
            return True
        
        # Check IP whitelist first
        client_ip = self._get_client_ip(request)
        if not self.check_ip_whitelist(client_ip):
            raise AuthorizationError(f"IP address {client_ip} not in whitelist")
        
        # Check rate limit
        client_id = f"{client_ip}:{request.headers.get('User-Agent', 'unknown')}"
        if not self.check_rate_limit(client_id):
            raise RateLimitError("Rate limit exceeded. Please try again later.")
        
        # Authenticate based on configured method
        if settings.HTTP_AUTH_METHOD == "api_key":
            return await self.authenticate_api_key(request)
        elif settings.HTTP_AUTH_METHOD == "bearer":
            return await self.authenticate_bearer(request)
        elif settings.HTTP_AUTH_METHOD == "basic":
            return await self.authenticate_basic(request)
        else:
            raise AuthenticationError(f"Unsupported authentication method: {settings.HTTP_AUTH_METHOD}")
    
    def _get_client_ip(self, request: Request) -> str:
        """Get the real client IP address with enhanced proxy support."""
        # When behind a trusted proxy, check forwarded headers
        if settings.BEHIND_PROXY and settings.TRUST_PROXY_HEADERS:
            # Check X-Forwarded-For header (most common)
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                # Take the first IP in the chain (original client)
                # Format: "client_ip, proxy1_ip, proxy2_ip"
                client_ip = forwarded_for.split(",")[0].strip()
                if self._is_valid_ip(client_ip):
                    return client_ip
            
            # Check X-Real-IP header (nginx specific)
            real_ip = request.headers.get("X-Real-IP")
            if real_ip and self._is_valid_ip(real_ip.strip()):
                return real_ip.strip()
            
            # Check CF-Connecting-IP header (CloudFlare)
            cf_connecting_ip = request.headers.get("CF-Connecting-IP")
            if cf_connecting_ip and self._is_valid_ip(cf_connecting_ip.strip()):
                return cf_connecting_ip.strip()
            
            # Check X-Forwarded header (less common)
            forwarded = request.headers.get("X-Forwarded")
            if forwarded:
                # Extract IP from format "for=ip;proto=https"
                parts = forwarded.split(";")
                for part in parts:
                    if part.strip().startswith("for="):
                        ip = part.split("=", 1)[1].strip()
                        if self._is_valid_ip(ip):
                            return ip
        
        # Fallback to direct client IP
        return request.client.host if request.client else "unknown"
    
    def _is_valid_ip(self, ip: str) -> bool:
        """Validate if string is a valid IP address."""
        try:
            # Remove port if present (IPv4:port or [IPv6]:port)
            if ':' in ip and not ip.startswith('['):
                ip = ip.split(':')[0]
            elif ip.startswith('[') and ']:' in ip:
                ip = ip.split(']:')[0][1:]
            
            ipaddress.ip_address(ip)
            return True
        except ValueError:
            return False

# Create a singleton instance
security_manager = SecurityManager()
