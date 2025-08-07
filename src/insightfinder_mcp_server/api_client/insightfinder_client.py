import httpx
import json
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from ..config.settings import settings

# Disable httpx info logging to reduce console output
logging.getLogger("httpx").setLevel(logging.WARNING)

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

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, params=params, headers=self.headers, timeout=100.0)
                response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
                
                raw_data = response.json()
                timeline_list = raw_data.get("timelineList", [])
                
                return {
                    "status": "success", 
                    "data": timeline_list,
                    "total_count": len(timeline_list),
                    "event_type": timeline_event_type
                }
            except httpx.HTTPStatusError as e:
                error_message = f"API request failed with status {e.response.status_code}: {e.response.text}"
                print(error_message) # For server-side logging
                return {"status": "error", "message": error_message}
            except httpx.RequestError as e:
                error_message = f"An error occurred while requesting {e.request.url!r}: {str(e)}"
                print(error_message) # For server-side logging
                return {"status": "error", "message": error_message}

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
