import httpx
import json
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from ..config.settings import settings

# Disable httpx info logging to reduce console output
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

class InsightFinderAPIClient:
    """
    A client for interacting with the InsightFinder API.
    """
    def __init__(self, system_name: str, user_name: str, license_key: str, api_url: str = "https://app.insightfinder.com"):
        self.base_url = api_url.rstrip("/") if api_url else "https://app.insightfinder.com"
        self.system_name = system_name
        self.user_name = user_name
        self.license_key = license_key
        self.headers = {
            "X-User-Name": self.user_name,
            "X-License-Key": self.license_key
        }

    async def _fetch_timeline_data(
        self,
        timeline_event_type: str,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Generic method to fetch timeline data from the InsightFinder API.
        
        Args:
            timeline_event_type: The type of timeline event (incident, trace, loganomaly, metricanomaly, deployment)
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing the API response data
        """
        api_path = "/api/v2/timeline"
        url = f"{self.base_url}{api_path}"
        
        params = {
            "systemName": system_name,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "timelineEventType": timeline_event_type
        }

        # Basic input validation
        if not system_name or len(system_name) > 100:
            return {"status": "error", "message": "Invalid system_name"}
        
        if end_time_ms - start_time_ms > 365 * 24 * 60 * 60 * 1000:  # Max 1 year
            return {"status": "error", "message": "Time range too large (max 1 year)"}

        async with httpx.AsyncClient(timeout=30.0) as client:  # Shorter timeout
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                
                # Check response size (prevent large payloads)
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB limit
                    return {"status": "error", "message": "Response too large"}
                
                raw_data = response.json()
                timeline_list = raw_data.get("timelineList", [])
                
                # Limit number of items to prevent memory issues
                if len(timeline_list) > 5000:
                    timeline_list = timeline_list[:5000]
                
                return {
                    "status": "success", 
                    "data": timeline_list,
                    "total_count": len(timeline_list),
                    "event_type": timeline_event_type
                }
            except httpx.HTTPStatusError as e:
                logger.error(f"API error {e.response.status_code} for {timeline_event_type}")
                return {"status": "error", "message": "API request failed"}
            except httpx.RequestError as e:
                logger.error(f"Network error for {timeline_event_type}: {str(e)}")
                return {"status": "error", "message": "Network error"}
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                return {"status": "error", "message": "Internal error"}

    async def get_incidents(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch incident timeline data from the InsightFinder API.
        
        Args:
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing incident timeline data
        """
        return await self._fetch_timeline_data("incident", system_name, start_time_ms, end_time_ms)

    async def get_traces(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch trace timeline data from the InsightFinder API.
        
        Args:
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing trace timeline data
        """
        return await self._fetch_timeline_data("trace", system_name, start_time_ms, end_time_ms)

    async def get_loganomaly(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch log anomaly timeline data from the InsightFinder API.
        
        Args:
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing log anomaly timeline data
        """
        return await self._fetch_timeline_data("loganomaly", system_name, start_time_ms, end_time_ms)

    async def get_metricanomaly(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch metric anomaly timeline data from the InsightFinder API.
        
        Args:
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing metric anomaly timeline data
        """
        result = await self._fetch_timeline_data("metricanomaly", system_name, start_time_ms, end_time_ms)
        return result

    async def get_deployment(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch deployment timeline data from the InsightFinder API.
        
        Args:
            system_name: The name of the system to query
            start_time_ms: The start of the time window in milliseconds since epoch
            end_time_ms: The end of the time window in milliseconds since epoch
            
        Returns:
            A dictionary containing deployment timeline data
        """
        return await self._fetch_timeline_data("deployment", system_name, start_time_ms, end_time_ms)

# Singleton instance of the API client
api_client = InsightFinderAPIClient(
    system_name=settings.INSIGHTFINDER_SYSTEM_NAME,
    user_name=settings.INSIGHTFINDER_USER_NAME,
    license_key=settings.INSIGHTFINDER_LICENSE_KEY,
    api_url=settings.INSIGHTFINDER_API_URL if hasattr(settings, 'INSIGHTFINDER_API_URL') and settings.INSIGHTFINDER_API_URL else "https://app.insightfinder.com"
)
