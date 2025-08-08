"""
Simple security utilities for the InsightFinder MCP Server.
"""

import time
import logging
from collections import defaultdict, deque
from typing import Dict, Any, Optional

from .config.settings import settings

logger = logging.getLogger(__name__)

class SimpleRateLimiter:
    """Basic rate limiter to prevent DDoS attacks."""
    
    def __init__(self, max_requests_per_minute: Optional[int] = None):
        self.max_requests = max_requests_per_minute or settings.MAX_REQUESTS_PER_MINUTE
        self.requests = defaultdict(deque)
    
    def is_allowed(self, client_id: str = "default") -> bool:
        """Check if request is allowed."""
        current_time = time.time()
        client_requests = self.requests[client_id]
        
        # Remove old requests (older than 1 minute)
        while client_requests and current_time - client_requests[0] > 60:
            client_requests.popleft()
        
        # Check if under limit
        if len(client_requests) >= self.max_requests:
            return False
        
        # Add current request
        client_requests.append(current_time)
        return True

class PayloadValidator:
    """Basic payload validation to prevent payload bombs."""
    
    @staticmethod
    def validate_size(data: Any, max_size: Optional[int] = None) -> bool:
        """Check if payload size is within limits."""
        max_size = max_size or settings.MAX_PAYLOAD_SIZE
        try:
            import json
            size = len(json.dumps(data) if not isinstance(data, str) else data)
            return size <= max_size
        except:
            return False
    
    @staticmethod
    def validate_string_length(text: str, max_length: int = 10000) -> str:
        """Truncate strings that are too long."""
        if len(text) > max_length:
            return text[:max_length] + "... [TRUNCATED]"
        return text

# Global instances
rate_limiter = SimpleRateLimiter()
validator = PayloadValidator()
