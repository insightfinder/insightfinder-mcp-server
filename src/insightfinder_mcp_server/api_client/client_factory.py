"""
Factory module for creating InsightFinder API client instances from HTTP request headers.
"""

from typing import Optional
from fastapi import Request, HTTPException
from .insightfinder_client import InsightFinderAPIClient, create_api_client
from .jira_client import JiraAPIClient, create_jira_client
from ..config.settings import settings
import contextvars


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


def extract_jira_credentials_from_headers(request: Request) -> Optional[dict]:
    """
    Extract JIRA credentials from HTTP headers.
    
    Expected headers:
    - X-JIRA-Server-URL: The JIRA server URL
    - X-JIRA-Username: The JIRA username/email
    - X-JIRA-API-Token: The JIRA API token
    
    Args:
        request: FastAPI Request object
        
    Returns:
        dict with JIRA credentials or None if not provided
    """
    server_url = request.headers.get("X-JIRA-Server-URL")
    username = request.headers.get("X-JIRA-Username")
    api_token = request.headers.get("X-JIRA-API-Token")
    
    if not all([server_url, username, api_token]):
        return None
    
    return {
        "server_url": server_url,
        "username": username,
        "api_token": api_token
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


# Global request context storage using contextvars for proper async/thread handling
_current_api_client: contextvars.ContextVar[Optional[InsightFinderAPIClient]] = contextvars.ContextVar('api_client', default=None)
_current_request: contextvars.ContextVar[Optional[Request]] = contextvars.ContextVar('request', default=None)
_current_jira_client: contextvars.ContextVar[Optional[JiraAPIClient]] = contextvars.ContextVar('jira_client', default=None)

def set_request_context(request: Request, api_client: InsightFinderAPIClient):
    """Store the current request context for tools to access.
    
    Uses contextvars which properly propagates across async boundaries,
    unlike thread-local storage which can fail when async tasks switch threads.
    """
    _current_request.set(request)
    _current_api_client.set(api_client)
    
    # Also try to create and set JIRA client if credentials are provided
    jira_credentials = extract_jira_credentials_from_headers(request)
    if jira_credentials:
        jira_client = create_jira_client(**jira_credentials)
        _current_jira_client.set(jira_client)
    else:
        _current_jira_client.set(None)

def get_current_api_client() -> Optional[InsightFinderAPIClient]:
    """Get the API client for the current request context.
    
    Uses contextvars which properly propagates across async boundaries.
    """
    return _current_api_client.get()

def clear_request_context():
    """Clear the current request context.
    
    This resets the context variables for the current async context.
    """
    _current_request.set(None)
    _current_api_client.set(None)
    _current_jira_client.set(None)
