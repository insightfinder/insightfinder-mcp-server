import httpx
import json
import logging
from datetime import datetime, timezone
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

    async def fetch_root_cause_analysis(
        self,
        root_cause_info_key: Dict[str, Any],
        customer_name: str
    ) -> Dict[str, Any]:
        """
        Fetch root cause analysis data for a specific incident.
        
        Args:
            root_cause_info_key: The rootCauseInfoKey object from the incident response
            customer_name: The customer/user name
            
        Returns:
            A dictionary containing the RCA chain data
        """
        api_path = "/api/v2/timeline-detail"
        url = f"{self.base_url}{api_path}"
        
        params = {
            "operation": "RCA",
            "customerName": customer_name,
            "queryString": json.dumps(root_cause_info_key)
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error fetching root cause analysis: {str(e)}")
            raise

    async def fetch_recommendation(
        self,
        incident_llm_key: Dict[str, Any],
        customer_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch recommendations for a specific incident.
        
        Args:
            incident_llm_key: The incidentLLMKey object from the incident response
            customer_name: The customer/user name
            
        Returns:
            A dictionary containing the recommendation data or None if not available
        """
        api_path = "/api/v2/timeline-detail"
        url = f"{self.base_url}{api_path}"
        
        params = {
            "operation": "Recommendation",
            "customerName": customer_name,
            "queryString": json.dumps(incident_llm_key)
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self.headers
                )
                response.raise_for_status()
                # Defensive: check if response is empty or not JSON
                if not response.text or not response.text.strip():
                    logger.warning(f"Empty response when fetching recommendations for incidentLLMKey: {incident_llm_key}")
                    return None
                try:
                    data = response.json()
                except Exception as json_err:
                    logger.warning(f"Non-JSON response when fetching recommendations: {response.text[:200]}")
                    return None
                # print(f"[DEBUG] Recommendations fetched: {data.get('recommendation', {}).get('response')}")
                return data.get("recommendation", {}).get("response")
        except Exception as e:
            logger.warning(f"Error fetching recommendations: {str(e)}")
            return None

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

        print(f"Fetching {timeline_event_type} data for {system_name} from {self.base_url} with params: {params}")
        
        # Debug: Display human-readable time range (timestamps are wall-clock in owner timezone)
        start_time_readable = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        end_time_readable = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        print(f"DEBUG: Time range - Start: {start_time_readable}, End: {end_time_readable} (Owner Timezone)")

        # Basic input validation
        if not system_name or len(system_name) > 100:
            return {"status": "error", "message": "Invalid system_name"}
        
        if end_time_ms - start_time_ms > 365 * 24 * 60 * 60 * 1000:  # Max 1 year
            return {"status": "error", "message": "Time range too large (max 1 year)"}

        print(f"Fetching {timeline_event_type} data for {system_name} from {self.base_url} with params: {params}")

        async with httpx.AsyncClient(timeout=60.0) as client:  # Increased timeout to 60 seconds
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                
                # Check response size (prevent large payloads)
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB limit
                    return {"status": "error", "message": "Response too large"}
                
                # Parse JSON with error handling
                try:
                    raw_data = response.json()
                except (json.JSONDecodeError, ValueError) as json_err:
                    response_text = response.text[:200] if response.text else "Empty response"
                    logger.error(f"JSON parse error for {timeline_event_type}: {json_err}. Response: {response_text}")
                    return {"status": "error", "message": f"Invalid JSON response: {response_text}"}
                    
                timeline_list = raw_data.get("timelineList", [])
                
                # Limit number of items to prevent memory issues
                if len(timeline_list) > 5000:
                    timeline_list = timeline_list[:5000]
                
                print(f"Successfully fetched {len(timeline_list)} {timeline_event_type} records for {system_name}")
                
                return {
                    "status": "success", 
                    "data": timeline_list,
                    "total_count": len(timeline_list),
                    "event_type": timeline_event_type
                }
            except httpx.HTTPStatusError as e:
                error_msg = f"API error {e.response.status_code} for {timeline_event_type}: {str(e)}"
                logger.error(error_msg)
                print(f"ERROR: {error_msg}")
                return {"status": "error", "message": f"API request failed: {e.response.status_code}"}
            except httpx.TimeoutException as e:
                error_msg = f"Timeout error for {timeline_event_type}: {str(e)}"
                logger.error(error_msg)
                print(f"ERROR: {error_msg}")
                return {"status": "error", "message": "Request timeout - API took too long to respond"}
            except httpx.RequestError as e:
                error_msg = f"Network error for {timeline_event_type}: {str(e)}"
                logger.error(error_msg)
                print(f"ERROR: {error_msg}")
                return {"status": "error", "message": f"Network error: {str(e)}"}
            except Exception as e:
                error_msg = f"Unexpected error in {timeline_event_type}: {str(e)}"
                logger.error(error_msg)
                print(f"ERROR: {error_msg}")
                return {"status": "error", "message": f"Internal error: {str(e)}"}

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
    
    async def predict_incidents(
        self,
        system_name: str,
        start_time_ms: int,
        end_time_ms: int
    ) -> dict:
        """
        Predict future incidents for a system in a given time window using the InsightFinder prediction API.
        Args:
            system_name (str): The name of the system to predict incidents for.
            start_time_ms (int): Start of the prediction window (owner timezone ms).
            end_time_ms (int): End of the prediction window (owner timezone ms).
        Returns:
            dict: API response containing predicted incidents (timelineList).
        """
        import httpx
        url = f"{self.base_url}/api/v2/timeline"
        params = {
            "systemName": system_name,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "timelineEventType": "incident",
            "predict": "true"
        }
        
        start_time_readable = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        end_time_readable = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        print(f"DEBUG: Time range - Start: {start_time_readable}, End: {end_time_readable} (Owner Timezone)")

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
                
                # Parse JSON with error handling
                try:
                    raw_data = response.json()
                except (json.JSONDecodeError, ValueError) as json_err:
                    response_text = response.text[:200] if response.text else "Empty response"
                    logger.error(f"JSON parse error for prediction api: {json_err}. Response: {response_text}")
                    return {"status": "error", "message": f"Invalid JSON response: {response_text}"}
                    
                timeline_list = raw_data.get("timelineList", [])
                
                # Limit number of items to prevent memory issues
                if len(timeline_list) > 5000:
                    timeline_list = timeline_list[:5000]
                
                return {
                    "status": "success", 
                    "data": timeline_list,
                    "total_count": len(timeline_list),
                    "event_type": "prediction"
                }
            except httpx.HTTPStatusError as e:
                logger.error(f"API error {e.response.status_code} for prediction api")
                return {"status": "error", "message": "API request failed"}
            except httpx.RequestError as e:
                logger.error(f"Network error for prediction api: {str(e)}")
                return {"status": "error", "message": "Network error"}
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                return {"status": "error", "message": "Internal error"}

    async def get_system_framework(self) -> Dict[str, Any]:
        """
        Get the complete system framework data including all owned and shared systems.
        
        Returns:
            A dictionary containing:
            - status: "success" or "error"
            - ownSystemArr: Array of owned system JSON strings
            - shareSystemArr: Array of shared system JSON strings
        """
        api_path = "/api/external/v1/systemframework"
        url = f"{self.base_url}{api_path}"
        
        params = {
            "customerName": self.user_name,
            "needDetail": "false",
            "tzOffset": "-18000000"  # Default timezone offset
        }
        
        # Note: This API uses X-API-Key instead of X-License-Key
        framework_headers = {
            "X-User-Name": self.user_name,
            "X-API-Key": self.license_key
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=framework_headers
                )
                response.raise_for_status()
                
                data = response.json()
                
                return {
                    "status": "success",
                    "ownSystemArr": data.get("ownSystemArr", []),
                    "shareSystemArr": data.get("shareSystemArr", [])
                }
                
        except httpx.HTTPStatusError as e:
            logger.error(f"API error {e.response.status_code} when fetching system framework")
            return {"status": "error", "message": f"API request failed with status {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"Network error when fetching system framework: {str(e)}")
            return {"status": "error", "message": "Network error"}
        except Exception as e:
            logger.error(f"Unexpected error fetching system framework: {str(e)}")
            return {"status": "error", "message": f"Internal error: {str(e)}"}

    async def get_customer_name_for_project(
        self,
        project_name: str
    ) -> Optional[tuple[str, str, str, List[str], str]]:
        """
        Get the customer/user name, actual project name, and instance list for a specific project by querying the system framework.
        
        This is necessary because projects can be owned by different users or shared across users.
        The method searches through both owned and shared systems to find the project by matching
        against either projectName or projectDisplayName, and returns the correct userName, projectName, and available instances.
        
        Args:
            project_name: Name or display name of the project to find (matches projectName or projectDisplayName)
            
        Returns:
            Tuple of (customer_name, actual_project_name, instance_list) if found, or None if not found
            - customer_name: The userName that owns the project
            - actual_project_name: The actual projectName (not display name) to use in API calls
            - instance_list: List of available instance names for this project
            - system_id: The system id that contains this project
        """
        api_path = "/api/external/v1/systemframework"
        url = f"{self.base_url}{api_path}"
        
        params = {
            "customerName": self.user_name,
            "needDetail": "true",
            "tzOffset": "-18000000"  # Default timezone offset
        }
        
        # Note: This API uses X-API-Key instead of X-License-Key
        framework_headers = {
            "X-User-Name": self.user_name,
            "X-API-Key": self.license_key
        }
        
        print(f"DEBUG: Fetching system framework for project '{project_name}'")
        print(f"DEBUG: System framework URL: {url}")
        print(f"DEBUG: System framework params: {params}")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=framework_headers
                )
                response.raise_for_status()
                
                data = response.json()
                
                # Search through owned systems
                for system_json in data.get("ownSystemArr", []):
                    try:
                        system_data = json.loads(system_json)
                        project_list_str = system_data.get("projectDetailsList", "[]")
                        project_list = json.loads(project_list_str)

                        systemKey_json = system_data.get("systemKey", "{}")
                        # Handle both dict and JSON string formats
                        if isinstance(systemKey_json, dict):
                            systemKey_data = systemKey_json
                        else:
                            systemKey_data = json.loads(systemKey_json)
                        system_id = systemKey_data.get("systemName", "")
                        
                        # Check each project in this system
                        for project in project_list:
                            # Match against both projectName and projectDisplayName (case-insensitive)
                            proj_name = project.get("projectName", "")
                            proj_display_name = project.get("projectDisplayName", "")
                            instance_list = project.get("instanceList", [])
                            
                            if (proj_name.lower() == project_name.lower() or 
                                proj_display_name.lower() == project_name.lower()):
                                customer_name = project.get("userName")
                                actual_project_name = proj_name  # Always use the actual projectName, not display name
                                logger.info(f"Found project '{project_name}' (actual: '{actual_project_name}') owned by customer '{customer_name}' with {len(instance_list)} instances")
                                return (customer_name, actual_project_name, proj_display_name, instance_list, system_id)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Error parsing owned system data: {e}")
                        continue
                
                # Search through shared systems
                for system_json in data.get("shareSystemArr", []):
                    try:
                        system_data = json.loads(system_json)
                        project_list_str = system_data.get("projectDetailsList", "[]")
                        project_list = json.loads(project_list_str)

                        systemKey_json = system_data.get("systemKey", "{}")
                        # Handle both dict and JSON string formats
                        if isinstance(systemKey_json, dict):
                            systemKey_data = systemKey_json
                        else:
                            systemKey_data = json.loads(systemKey_json)
                        system_id = systemKey_data.get("systemName", "")

                        # Check each project in this system
                        for project in project_list:
                            # Match against both projectName and projectDisplayName (case-insensitive)
                            proj_name = project.get("projectName", "")
                            proj_display_name = project.get("projectDisplayName", "")
                            instance_list = project.get("instanceList", [])
                            
                            if (proj_name.lower() == project_name.lower() or 
                                proj_display_name.lower() == project_name.lower()):
                                customer_name = project.get("userName")
                                actual_project_name = proj_name  # Always use the actual projectName, not display name
                                logger.info(f"Found project '{project_name}' (actual: '{actual_project_name}') shared from customer '{customer_name}' with {len(instance_list)} instances")
                                return (customer_name, actual_project_name, proj_display_name, instance_list, system_id)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Error parsing shared system data: {e}")
                        continue
                
                logger.warning(f"Project '{project_name}' not found in system framework")
                return None
                
        except httpx.HTTPStatusError as e:
            logger.error(f"API error {e.response.status_code} when fetching system framework")
            return None
        except httpx.RequestError as e:
            logger.error(f"Network error when fetching system framework: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching system framework: {str(e)}")
            return None

    async def get_metric_data(
        self,
        project_name: str,
        instance_name: str,
        metric_list: List[str],
        start_time_ms: int,
        end_time_ms: int
    ) -> Dict[str, Any]:
        """
        Fetch metric time-series data for specified metrics and instance.
        
        Args:
            project_name: Name of the project to query
            instance_name: Name of the instance/host to query
            metric_list: List of metric names to fetch
            start_time_ms: Start timestamp in milliseconds
            end_time_ms: End timestamp in milliseconds
            
        Returns:
            A dictionary containing the API response with metric data
        """
        # Ensure timestamps are integers (they might come in as strings from JSON)
        start_time_ms = int(start_time_ms) if isinstance(start_time_ms, str) else start_time_ms
        end_time_ms = int(end_time_ms) if isinstance(end_time_ms, str) else end_time_ms
        
        api_path = "/api/v1/metricdataquery-external"
        url = f"{self.base_url}{api_path}"
        
        # Get the correct customer name and actual project name for this project
        project_info = await self.get_customer_name_for_project(project_name)
        if project_info:
            customer_name, actual_project_name, display_project_name, instance_list, system_id = project_info
            # Use the actual project name returned from the API (not the display name)
            project_name = actual_project_name
        else:
            logger.warning(f"Could not find owner for project '{project_name}', falling back to self.user_name")
            customer_name = self.user_name
            instance_list = []
            # Keep the provided project_name as fallback
        
        # Format metric list as JSON array string for URL parameter
        metric_list_json = json.dumps(metric_list)
        
        params = {
            "customerName": customer_name,
            "projectName": project_name,
            "instanceName": instance_name,
            "metricList": metric_list_json,
            "startTime": start_time_ms,
            "endTime": end_time_ms
        }
        
        logger.info(f"Fetching metric data for project={project_name}, instance={instance_name}, "
                   f"metrics={metric_list}, customer={customer_name}")
        
        # Debug: Display human-readable time range (timestamps are wall-clock in owner timezone)
        start_time_readable = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        end_time_readable = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        print(f"DEBUG: Metric data time range - Start: {start_time_readable}, End: {end_time_readable} (Owner Timezone)")
        # print(f"DEBUG: Metric data customer: {customer_name} (logged-in user: {self.user_name})")
        # print(f"DEBUG: Metric data API URL: {url}")
        # print(f"DEBUG: Metric data params: {params}")
        
        # Basic input validation
        if not project_name or not instance_name:
            return {"status": "error", "message": "project_name and instance_name are required"}
        
        if not metric_list or len(metric_list) == 0:
            return {"status": "error", "message": "metric_list must contain at least one metric"}
        
        # Validate instance name if we have the instance list
        if instance_list and instance_name not in instance_list:
            return {
                "status": "error",
                "message": f"Invalid instance_name '{instance_name}'. This instance is not available in project '{project_name}'.",
                "invalidInstance": instance_name,
                "availableInstances": instance_list[:50],  # Show first 50 available instances
                "totalAvailableInstances": len(instance_list),
                "hint": f"Use list_available_instances_for_project tool to see all {len(instance_list)} available instances for this project."
            }
        
        if end_time_ms - start_time_ms > 365 * 24 * 60 * 60 * 1000:  # Max 1 year
            return {"status": "error", "message": "Time range too large (max 1 year)"}
        
        try:
            # Build the full URL with query parameters
            from urllib.parse import urlencode
            query_string = urlencode(params)
            full_url = f"{url}?{query_string}"
            
            async with httpx.AsyncClient(timeout=60.0) as client:  # Longer timeout for potentially large data
                response = await client.get(
                    url,
                    params=params,
                    headers=self.headers
                )
                response.raise_for_status()
                
                # Check response size
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > 50 * 1024 * 1024:  # 50MB limit
                    return {"status": "error", "message": "Response too large (>50MB)"}
                
                # Parse JSON
                try:
                    raw_data = response.json()
                except (json.JSONDecodeError, ValueError) as json_err:
                    response_text = response.text[:200] if response.text else "Empty response"
                    logger.error(f"JSON parse error for metric data: {json_err}. Response: {response_text}")
                    return {"status": "error", "message": f"Invalid JSON response: {response_text}"}
                
                # Validate response structure
                if not isinstance(raw_data, list):
                    return {"status": "error", "message": "Unexpected response format (expected list)"}
                
                return {
                    "status": "success",
                    "data": raw_data,
                    "total_metrics": len(raw_data),
                    "url": full_url
                }
                
        except httpx.HTTPStatusError as e:
            logger.error(f"API error {e.response.status_code} for metric data query")
            return {"status": "error", "message": f"API request failed with status {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"Network error for metric data query: {str(e)}")
            return {"status": "error", "message": "Network error"}
        except Exception as e:
            logger.error(f"Unexpected error fetching metric data: {str(e)}")
            return {"status": "error", "message": f"Internal error: {str(e)}"}

    async def get_metric_metadata(
        self,
        project_name: str
    ) -> Dict[str, Any]:
        """
        Fetch available metrics metadata for a project.
        
        Args:
            project_name: Name of the project to query
            
        Returns:
            A dictionary containing the API response with available metric list
        """
        api_path = "/api/v1/metricmetadata-external"
        url = f"{self.base_url}{api_path}"
        
        # Get the correct customer name and actual project name for this project
        project_info = await self.get_customer_name_for_project(project_name)
        if project_info:
            customer_name, actual_project_name, display_project_name, instance_list, system_id = project_info
            # Use the actual project name returned from the API (not the display name)
            project_name = actual_project_name
        else:
            logger.warning(f"Could not find owner for project '{project_name}', falling back to self.user_name")
            customer_name = self.user_name
            instance_list = []
            # Keep the provided project_name as fallback
        
        params = {
            "customerName": customer_name,
            "projectName": project_name
        }
        
        logger.info(f"Fetching metric metadata for project={project_name}, customer={customer_name}")
        
        # print(f"DEBUG: Metric metadata customer: {customer_name} (logged-in user: {self.user_name})")
        # print(f"DEBUG: Metric metadata API URL: {url}")
        # print(f"DEBUG: Metric metadata params: {params}")
        
        # Basic input validation
        if not project_name:
            return {"status": "error", "message": "project_name is required"}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=self.headers
                )
                response.raise_for_status()
                
                # Parse JSON
                try:
                    raw_data = response.json()
                except (json.JSONDecodeError, ValueError) as json_err:
                    response_text = response.text[:200] if response.text else "Empty response"
                    logger.error(f"JSON parse error for metric metadata: {json_err}. Response: {response_text}")
                    return {"status": "error", "message": f"Invalid JSON response: {response_text}"}
                
                # Validate response structure
                if not isinstance(raw_data, dict):
                    return {"status": "error", "message": "Unexpected response format (expected dict)"}
                
                return {
                    "status": "success",
                    "data": raw_data
                }
                
        except httpx.HTTPStatusError as e:
            logger.error(f"API error {e.response.status_code} for metric metadata query")
            return {"status": "error", "message": f"API request failed with status {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"Network error for metric metadata query: {str(e)}")
            return {"status": "error", "message": "Network error"}
        except Exception as e:
            logger.error(f"Unexpected error fetching metric metadata: {str(e)}")
            return {"status": "error", "message": f"Internal error: {str(e)}"}

    async def create_jira_ticket(
        self,
        customer_name: str,
        project_key: str,
        jira_assignee_id: str,
        jira_reporter_id: str,
        summary: str,
        project_name: str = "jira ticket created by mcp server",
        pattern_id: int = 0,
        anomaly_score: int = 0,
    raw_data: str = "",
    jira_issue_fields: str | Dict[str, Any] = '{"fixVersions":"10019"}'
    ) -> Dict[str, Any]:
        """Create a Jira ticket via InsightFinder jiraPostEvent endpoint.

        Args:
            customer_name: InsightFinder customer/user name (maps to customerName)
            project_key: Jira project key (projectKey)
            jira_assignee_id: Jira account id for assignee (jiraAssigneeId)
            jira_reporter_id: Jira account id for reporter (jiraReporterId)
            summary: Ticket summary/title (summary)
            project_name: InsightFinder projectName (default fixed string)
            pattern_id: Pattern identifier (patternId) default 0
            anomaly_score: Anomaly score (anomalyScore) default 0
            raw_data: Description/body text (rawData)

        Returns:
            Dict with status and response content.
        """
        api_path = "/api/v1/jiraPostEvent"
        url = f"{self.base_url}{api_path}"

        # Allow caller to pass jira_issue_fields either as JSON string or dict
        if isinstance(jira_issue_fields, dict):
            try:
                jira_issue_fields_str = json.dumps(jira_issue_fields, separators=(",", ":"))
            except Exception:
                jira_issue_fields_str = '{}'
        else:
            jira_issue_fields_str = jira_issue_fields

        params = {
            "projectKey": project_key,
            "jiraAssigneeId": jira_assignee_id,
            "jiraReporterId": jira_reporter_id,
            "jiraIssueFields": jira_issue_fields_str,
            "summary": summary,
            "customerName": customer_name,
            "projectName": project_name,
            "patternId": str(pattern_id),  # ensure string per API examples
            "anomalyScore": str(anomaly_score),
            "rawData": raw_data
        }

        # Allow optional issue fields extension through future parameter but keep simple now
        try:
            # Manual URL encoding to satisfy requirement of building full URL with query string
            from urllib.parse import urlencode
            query_string = urlencode(params, safe="")
            full_url = f"{url}?{query_string}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Send as POST with all parameters in URL (no body) matching provided pattern
                response = await client.post(full_url, headers=self.headers)
                # Some older endpoints may return 200 even on logical failure; capture body
                text = response.text
                http_status_ok = True
                try:
                    response.raise_for_status()
                except Exception:
                    # We still attempt to parse body even if HTTP error
                    http_status_ok = False
                # Attempt JSON parse but fallback to raw text
                try:
                    body = response.json()
                except Exception:
                    body = {"raw": text}

                # Determine success strictly from the API's SUCCESS flag if present
                success_flag = None
                if isinstance(body, dict) and "SUCCESS" in body:
                    success_flag = bool(body.get("SUCCESS"))

                # Status precedence: use SUCCESS flag when available; otherwise fall back to HTTP status
                if success_flag is not None:
                    status = "success" if success_flag else "error"
                else:
                    status = "success" if http_status_ok else "error"

                return {
                    "status": status,
                    "success": success_flag if success_flag is not None else (True if status == "success" else False),
                    "response": body,
                    "http_status": response.status_code,
                }
        except Exception as e:
            logger.error(f"Error creating Jira ticket: {e}")
            return {"status": "error", "message": str(e)}

# Factory function to create API client instances with provided credentials
def create_api_client(license_key: str, user_name: str, system_name: Optional[str] = None, api_url: Optional[str] = None) -> InsightFinderAPIClient:
    """
    Create an InsightFinder API client instance with the provided credentials.
    
    Args:
        license_key: The InsightFinder license key (from HTTP header)
        user_name: The InsightFinder username (from HTTP header)  
        system_name: Optional system name (from HTTP header, not required)
        api_url: Optional API URL (defaults to settings.INSIGHTFINDER_API_URL)
    
    Returns:
        InsightFinderAPIClient instance configured with the provided credentials
    """
    return InsightFinderAPIClient(
        system_name=system_name or "",  # Not required for now
        user_name=user_name,
        license_key=license_key,
        api_url=api_url or settings.INSIGHTFINDER_API_URL
    )

# Legacy singleton instance - deprecated, tools should use create_api_client instead
# This is kept for backwards compatibility but will be removed
api_client = None  # No longer creating a singleton instance
