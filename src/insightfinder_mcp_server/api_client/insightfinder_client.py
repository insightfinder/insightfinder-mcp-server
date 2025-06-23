import httpx
import json
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from ..config.settings import settings

class InsightFinderAPIClient:
    """
    A client for interacting with the InsightFinder API.
    """
    def __init__(self, api_url: str, jwt_token: str, system_name: str, user_name: str):
        self.base_url = api_url.rstrip("/") if api_url else api_url
        self.jwt_token = jwt_token
        self.system_name = system_name
        self.user_name = user_name
        self.headers = {
            "Authorization": f"Bearer {self.jwt_token}"
        }

    def _extract_metric_root_causes(self, response_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract and parse all metricRootCause values from the response.
        
        Args:
            response_data: The raw API response data
            
        Returns:
            A list of parsed metricRootCause objects
        """
        metric_root_causes = []
        
        for item in response_data:
            if "anomalyTimelines" in item:
                for timeline in item["anomalyTimelines"]:
                    if "metricRootCause" in timeline and timeline["metricRootCause"]:
                        try:
                            # Parse the JSON string in metricRootCause
                            root_cause_data = json.loads(timeline["metricRootCause"])
                            metric_root_causes.append(root_cause_data)
                        except json.JSONDecodeError:
                            # If JSON parsing fails, skip this entry
                            continue
        
        return metric_root_causes

    async def get_root_cause_timelines(
        self,
        zone_name: Optional[str] = None,
        zone_list: Optional[List[str]] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        environment_name: str = "All",
        include_past_occurrence: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetches incident timelines from the InsightFinder rootcausetimelinesJWT API.

        Args:
            system_name: The name of the system to query.
            customer_name: The customer name for the request.
            subdomain: The subdomain to use for the API URL.
            zone_name: The zone name (defaults to all_zone_{system_name}).
            zone_list: List of zones to query.
            start_time_ms: The start of the time window in milliseconds since epoch.
            end_time_ms: The end of the time window in milliseconds since epoch.
            environment_name: The environment name to filter by.
            include_past_occurrence: Whether to include past occurrences.

        Returns:
            A dictionary containing the extracted metricRootCause data.
        """
        api_path = "/api/v2/rootcausetimelinesJWT"
        url = f"{self.base_url}{api_path}"
        
        # Set defaults
        if zone_name is None:
            zone_name = f"all_zone_{self.system_name}"
        if zone_list is None:
            zone_list = []
        
        params = {
            "systemName": self.system_name,
            "customerName": self.user_name,
            "jwt": self.jwt_token,  # JWT token is passed as query parameter
            "zoneName": zone_name,
            "zoneList": str(zone_list),  # Convert list to string representation
            "environmentName": environment_name,
            "includePastOccurrence": str(include_past_occurrence).lower(),
        }
        
        # Add time parameters if provided
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, params=params, timeout=100.0)
                response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
                
                # Extract metricRootCause data from the response
                raw_data = response.json()
                metric_root_causes = self._extract_metric_root_causes(raw_data)
                
                return {"status": "success", "data": metric_root_causes[:100]}
            except httpx.HTTPStatusError as e:
                error_message = f"API request failed with status {e.response.status_code}: {e.response.text}"
                print(error_message) # For server-side logging
                return {"status": "error", "message": error_message}
            except httpx.RequestError as e:
                error_message = f"An error occurred while requesting {e.request.url!r}: {str(e)}"
                print(error_message) # For server-side logging
                return {"status": "error", "message": error_message}

# Singleton instance of the API client
api_client = InsightFinderAPIClient(
    api_url=settings.INSIGHTFINDER_API_URL,
    jwt_token=settings.INSIGHTFINDER_JWT_TOKEN,
    system_name=settings.INSIGHTFINDER_SYSTEM_NAME,
    user_name=settings.INSIGHTFINDER_USER_NAME
)
