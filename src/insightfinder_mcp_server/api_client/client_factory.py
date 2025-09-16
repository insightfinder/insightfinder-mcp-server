"""
Factory module for creating InsightFinder API client instances from HTTP request headers.
"""

from typing import Optional
from fastapi import Request, HTTPException
from .insightfinder_client import InsightFinderAPIClient, create_api_client
from ..config.settings import settings


def extract_insightfinder_credentials_from_headers(request: Request) -> dict:
    """
    Extract InsightFinder credentials from HTTP headers.
    
    Expected headers:
    - X-IF-License-Key: The license key
    - X-IF-User-Name: The username
    - X-IF-API-URL: Optional API URL (defaults to settings)
    
    Args:
        request: FastAPI Request object
        
    Returns:
        dict with credentials: {license_key, user_name, api_url?}
        
    Raises:
        HTTPException: If required headers are missing
    """
    # Support both new (X-IF-*) and legacy header names
    license_key = request.headers.get("X-IF-License-Key") or request.headers.get("X-License-Key")
    user_name = request.headers.get("X-IF-User-Name") or request.headers.get("X-User-Name")
    api_url = request.headers.get("X-IF-API-URL")  # Optional
    
    if not license_key:
        raise HTTPException(
            status_code=400,
            detail="Missing required header: X-IF-License-Key"
        )
    
    if not user_name:
        raise HTTPException(
            status_code=400,
            detail="Missing required header: X-IF-User-Name"  
        )
    
    return {
        "license_key": license_key,
        "user_name": user_name,
        "api_url": api_url or settings.INSIGHTFINDER_API_URL
    }


def create_api_client_from_request(request: Request) -> InsightFinderAPIClient:
    """
    Create an InsightFinder API client from HTTP request headers.
    
    Args:
        request: FastAPI Request object with InsightFinder credentials in headers
        
    Returns:
        InsightFinderAPIClient configured with credentials from headers
        
    Raises:
        HTTPException: If required headers are missing
    """
    credentials = extract_insightfinder_credentials_from_headers(request)
    return create_api_client(**credentials)


# Global request context storage for accessing API client in tools
_current_request_context = {}

def set_request_context(request: Request, api_client: InsightFinderAPIClient):
    """Store the current request context for tools to access."""
    import threading
    thread_id = threading.get_ident()
    _current_request_context[thread_id] = {
        "request": request,
        "api_client": api_client
    }

def get_current_api_client() -> Optional[InsightFinderAPIClient]:
    """Get the API client for the current request context."""
    import threading
    thread_id = threading.get_ident()
    context = _current_request_context.get(thread_id)
    return context.get("api_client") if context else None

def clear_request_context():
    """Clear the current request context."""
    import threading
    thread_id = threading.get_ident()
    _current_request_context.pop(thread_id, None)
