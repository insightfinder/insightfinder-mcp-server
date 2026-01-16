"""
Metric data tools for the InsightFinder MCP server.

This module provides tools for fetching and analyzing metric time-series data:
- get_metric_data: Fetch metric line chart data for specified metrics and instances
- list_available_metrics: List all available metrics for a project

These tools help users analyze metric trends, patterns, and historical data points.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from urllib.parse import quote


from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import get_timezone_aware_time_range_ms, format_timestamp_in_user_timezone

logger = logging.getLogger(__name__)

# ============================================================================
# METRIC DATA TOOLS
# ============================================================================

@mcp_server.tool()
async def get_metric_data(
    project_name: str,
    instance_name: str,
    metric_list: List[str],
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get the API URL and UI URL for fetching metric line chart data for specified metrics and instance.
    This tool validates the request by making an API call to InsightFinder, then returns
    the API URL and UI URL that users can click to directly access the JSON metric data in their browser.

    IMPORTANT: Always show both API URL and UI URL in the response for user. Both are crucial for
    accessing and visualizing the metric data.

    **Important Time Range Requirements:**
    - start_time and end_time accept ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
    - start_time must be LESS than end_time (start time must come before end time)
    - start_time and end_time cannot be the same value
    
    **IMPORTANT - When Fetching Metrics for Incidents:**
    - When investigating incidents or working with incident data, ALWAYS use the metric_name and 
      instance_name directly from the incident response (from tools like get_incident_details, 
      get_incidents_list, get_incidents_summary, etc.)
    - DO NOT call validate_instance_name or validate_metric_name unnecessarily when you already 
      have validated data from incident tools
    - DO NOT call list_available_instances_for_project or list_available_metrics when working 
      with incident data - the incident already contains the correct metric and instance names
    - Only call validation tools if the metric/instance is NOT coming from an incident response,
      or if you get an error indicating invalid metric/instance
    
    **Instance Validation (for non-incident queries):**
    - If you already know the instance_name (and it's NOT from an incident), validate it FIRST 
      using validate_instance_name tool by passing project_name and instance_name before calling this tool.
    - If validation fails or you don't know the instance name, use list_available_instances_for_project
      to see all available instances for the project.
    - Only call get_metric_data after confirming the instance exists.
    
    **Metric Validation (for non-incident queries):**
    - If you already know the metric names (and they're NOT from an incident), validate them FIRST 
      using validate_metric_name tool by passing project_name and metric_list before calling this tool.
    - If validation fails or you don't know the metric names, use list_available_metrics
      to see all available metrics for the project.
    - Only call get_metric_data after confirming the metrics exist.
    
    **Recommended Workflow:**
    
    FOR INCIDENT-RELATED METRICS (Most Common):
    1. Get incident data using incident tools (get_incident_details, get_incidents_list, etc.)
    2. Extract metric_name and instance_name directly from the incident response
    3. Call get_metric_data DIRECTLY with the incident's metric and instance
       - The function will automatically validate the data internally
       - NO need to call validate_instance_name or validate_metric_name tools separately
    
    FOR GENERAL METRIC QUERIES (Non-incident):
    1. Use validate_instance_name to check if instance exists (if you know the instance name)
       OR use list_available_instances_for_project to discover instances
    2. Use validate_metric_name to check if metrics exist (if you know the metric names)
       OR use list_available_metrics to discover available metrics
    3. Call get_metric_data with validated instance and metrics
    
    **When to use this tool:**
    - When user wants to see metric trends over time
    - To get a direct link to metric data JSON
    - To visualize metric performance and patterns
    - To analyze historical metric values for a specific instance
    - To compare multiple metrics side-by-side
    - To investigate incident root causes by viewing related metrics
    
    Args:
        project_name: Name of the project to query (required)
        instance_name: Name of the specific instance/host to query (required)
                      - Can be taken directly from incident data (recommended for incident investigation)
                      - Should be validated with validate_instance_name tool for non-incident queries
        metric_list: List of metric names to fetch data for (e.g., ["Availability", "CPU", "Memory"])
                    - Can be taken directly from incident data (recommended for incident investigation)
                    - Should be validated with validate_metric_name tool for non-incident queries
        start_time: Start timestamp in ISO 8601 format (e.g., "2026-01-08T21:45:30Z") 
        end_time: End timestamp in ISO 8601 format (e.g., "2026-01-08T21:45:30Z")

    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - api-url: Direct API URL to access the metric data JSON (click to view in browser)
        - ui-url: Direct URL to view the metric data in InsightFinder UI
        - metadata: Query parameters and time range information
        
    Example:
        # RECOMMENDED: For incident investigation (NO validation needed)
        # Step 1: Get incident details
        incident = await get_incident_details(
            system_name="my-system",
            incident_timestamp="1768522080000"
        )
        
        # Step 2: Extract metric and instance from incident
        metric_name = incident.get("metric_name")  # e.g., "cron-globalView"
        instance_name = incident["incident"]["instanceName"]  # e.g., "bitnami-rabbitmq-0"
        project_name = incident.get("projectDisplayName")  # e.g., "Prod RMQ Queue Length"
        
        # Step 3: Fetch metric data DIRECTLY (validation happens automatically inside the function)
        result = await get_metric_data(
            project_name=project_name,
            instance_name=instance_name,
            metric_list=[metric_name],
            start_time="2026-01-08T21:45:30Z",
            end_time="2026-01-09T21:45:30Z"
        )
        
        # For general metric queries (WITH validation)
        # Step 1: Validate instance
        instance_check = await validate_instance_name(
            project_name="my-project",
            instance_name="server-01"
        )
        
        # Step 2: Validate metrics
        metrics_check = await validate_metric_name(
            project_name="my-project",
            metric_list=["CPU", "Memory"]
        )
        
        # Step 3: Get metric data (if validations passed)
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
    """
    try:
        # Get current API client
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        # Convert timestamps to milliseconds
        start_time_ms = None
        end_time_ms = None
        
        # Get time range with timezone awareness if not provided
        if start_time is None or end_time is None:
            start_time_ms, end_time_ms = get_timezone_aware_time_range_ms(days_back=1)
        else:
            # Convert start_time
            if isinstance(start_time, str):
                # Try ISO 8601 format first
                if 'T' in start_time or '-' in start_time:
                    try:
                        # Parse ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
                        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        start_time_ms = int(dt.timestamp() * 1000)
                    except ValueError:
                        return {
                            "status": "error",
                            "message": f"Invalid ISO 8601 timestamp format for start_time: '{start_time}'. Expected format: '2026-01-08T21:45:30Z'"
                        }
                else:
                    # Try to parse as 13-digit milliseconds string
                    try:
                        start_time_ms = int(start_time)
                    except ValueError:
                        return {
                            "status": "error",
                            "message": f"Invalid start_time: must be ISO 8601 format or 13-digit milliseconds, got '{start_time}'"
                        }
            elif isinstance(start_time, int):
                start_time_ms = start_time
            else:
                return {
                    "status": "error",
                    "message": f"Invalid start_time type: {type(start_time).__name__}"
                }
            
            # Convert end_time
            if isinstance(end_time, str):
                # Try ISO 8601 format first
                if 'T' in end_time or '-' in end_time:
                    try:
                        # Parse ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
                        dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        end_time_ms = int(dt.timestamp() * 1000)
                    except ValueError:
                        return {
                            "status": "error",
                            "message": f"Invalid ISO 8601 timestamp format for end_time: '{end_time}'. Expected format: '2026-01-08T21:45:30Z'"
                        }
                else:
                    # Try to parse as 13-digit milliseconds string
                    try:
                        end_time_ms = int(end_time)
                    except ValueError:
                        return {
                            "status": "error",
                            "message": f"Invalid end_time: must be ISO 8601 format or 13-digit milliseconds, got '{end_time}'"
                        }
            elif isinstance(end_time, int):
                end_time_ms = end_time
            else:
                return {
                    "status": "error",
                    "message": f"Invalid end_time type: {type(end_time).__name__}"
                }
        
        
        # Ensure timestamps are not None after assignment
        if start_time_ms is None or end_time_ms is None:
            return {
                "status": "error",
                "message": "Failed to determine valid time range"
            }
        
        # Validate time range - start and end cannot be the same
        if start_time_ms == end_time_ms:
            return {
                "status": "error",
                "message": f"Invalid time range: start_time_ms and end_time_ms cannot be the same value ({start_time_ms}). Please provide a valid time range where start_time_ms < end_time_ms."
            }
        
        # Validate time range - start must be before end
        if start_time_ms > end_time_ms:
            return {
                "status": "error",
                "message": f"Invalid time range: start_time_ms ({start_time_ms}) must be less than end_time_ms ({end_time_ms}). Start time must come before end time."
            }
        
        # Validate inputs
        if not project_name or not instance_name:
            return {
                "status": "error",
                "message": "project_name and instance_name are required parameters"
            }
        
        if not metric_list or len(metric_list) == 0:
            return {
                "status": "error",
                "message": "metric_list must contain at least one metric name"
            }
        
        # Get project info to validate both metrics and instances
        logger.info(f"Validating project info for project={project_name}")
        
        # Get project information including instance list
        project_info = await api_client.get_customer_name_for_project(project_name)
        if not project_info:
            return {
                "status": "error",
                "message": f"Project '{project_name}' not found. Please verify the project name or use list_all_systems_and_projects to see available projects."
            }
        
        customer_name, actual_project_name, instance_list, system_id = project_info
        
        # Validate instance name if we have the instance list
        if instance_list and instance_name not in instance_list:
            return {
                "status": "error",
                "message": f"Invalid instance_name '{instance_name}'. This instance is not available in project '{actual_project_name}'.",
                "invalidInstance": instance_name,
                "availableInstances": instance_list[:50],  # Show first 50 available instances
                "totalAvailableInstances": len(instance_list),
                "hint": f"Use list_available_instances_for_project tool to see all {len(instance_list)} available instances for this project."
            }
        
        # Validate that requested metrics are available in the project
        logger.info(f"Validating metrics for project={project_name}")
        metadata_result = await api_client.get_metric_metadata(project_name=actual_project_name)
        
        if metadata_result.get("status") == "error":
            return {
                "status": "error",
                "message": f"Failed to validate metrics: {metadata_result.get('message', 'Unknown error')}"
            }
        
        # Get available metrics
        raw_metadata = metadata_result.get("data", {})
        available_metrics = raw_metadata.get("possibleMetricList", [])
        
        if not available_metrics:
            return {
                "status": "error",
                "message": f"No metrics available for project '{project_name}'. The project may not have any metric data or the project name may be incorrect."
            }
        
        # Check if all requested metrics are available
        invalid_metrics = [metric for metric in metric_list if metric not in available_metrics]
        
        if invalid_metrics:
            return {
                "status": "error",
                "message": f"Invalid metric(s) requested: {invalid_metrics}. These metrics are not available in project '{project_name}'.",
                "invalidMetrics": invalid_metrics,
                "availableMetrics": available_metrics[:20],  # Show first 20 available metrics
                "totalAvailableMetrics": len(available_metrics),
                "hint": f"Use list_available_metrics tool to see all {len(available_metrics)} available metrics for this project."
            }

        logger.info(f"Fetching metric data URL for project={project_name}, instance={instance_name}, "
              f"metrics={metric_list}")

        # Fetch metric data using the API client to validate the request works
        result = await api_client.get_metric_data(
            project_name=actual_project_name,
            instance_name=instance_name,
            metric_list=metric_list,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if result.get("status") == "error":
            return result
        
        # Check if response data is empty
        response_data = result.get("data", [])
        if not response_data or len(response_data) == 0:
            # Format timestamps for error message
            start_time_formatted = format_timestamp_in_user_timezone(start_time_ms)
            end_time_formatted = format_timestamp_in_user_timezone(end_time_ms)
            
            return {
                "status": "error",
                "message": f"No data available for the given time range. No metric data found for project '{project_name}', instance '{instance_name}', metrics {metric_list} between {start_time_formatted} and {end_time_formatted}. Please verify the time range, instance name, and metric names are correct.",
                "metadata": {
                    "projectName": project_name,
                    "instanceName": instance_name,
                    "requestedMetrics": metric_list,
                    "timeRange": {
                        "startTime": start_time_ms,
                        "endTime": end_time_ms,
                        "startTimeFormatted": start_time_formatted,
                        "endTimeFormatted": end_time_formatted
                    }
                }
            }
        
        # Extract the URL from the result
        api_url = result.get("url")
        if not api_url:
            return {
                "status": "error",
                "message": "Failed to generate API URL"
            }
        
        # Format timestamps for display
        start_time_formatted = format_timestamp_in_user_timezone(start_time_ms)
        end_time_formatted = format_timestamp_in_user_timezone(end_time_ms)

        # extract base url like https://api.insightfinder.com --- IGNORE ---
        base_api_url = api_url.split("/api/")[0]  # e.g., https://api.insightfinder.com --- IGNORE ---
        
        # URL encode parameters to handle spaces and special characters
        encoded_system_id = quote(system_id, safe='')
        encoded_customer_name = quote(customer_name, safe='')
        encoded_project_name = quote(actual_project_name, safe='')
        encoded_instance_name = quote(instance_name, safe='')
        encoded_metrics = quote(','.join(metric_list), safe='')
        
        ui_url = f"{base_api_url}/ui/metric/linecharts?e=All&s={encoded_system_id}&customerName={encoded_customer_name}&projectName={encoded_project_name}@{encoded_customer_name}&startTimestamp={start_time_ms}&endTimestamp={end_time_ms}&justSelectMetric={encoded_metrics}&sessionMetric=&justInstanceList={encoded_instance_name}&withBaseline=true&incidentInfo=&sourceInfo=&metricAnomalyMap="

        return {
            "status": "success",
            "api-url": api_url+f"&projectDisplayName={project_name}",
            "ui-url": ui_url,
            "message": "Click the URL to view the metric data JSON in your browser",
            "metadata": {
                "projectName": project_name,
                "instanceName": instance_name,
                "requestedMetrics": metric_list,
                "timeRange": {
                    "startTime": start_time_ms,
                    "endTime": end_time_ms,
                    "startTimeFormatted": start_time_formatted,
                    "endTimeFormatted": end_time_formatted
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching metric data URL: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch metric data URL: {str(e)}"
        }


@mcp_server.tool()
async def list_available_metrics(
    project_name: str
) -> Dict[str, Any]:
    """
    List all available metrics for a given project.
    
    This tool retrieves the complete list of metric names that are available
    for querying within a specific project. Use this to discover what metrics
    can be passed to the get_metric_data tool.
    
    **When to use this tool:**
    - Before querying metric data, to see what metrics are available
    - When user asks "what metrics can I query?"
    - To help users understand the monitoring coverage of their project
    - When users need to know the exact metric names for querying
    
    **Workflow:**
    1. Use this tool to get the list of available metrics
    2. User selects metrics of interest from the list
    3. Use get_metric_data tool with the selected metrics
    
    Args:
        project_name: Name of the project to query (required)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - projectName: Name of the queried project
        - availableMetrics: List of metric names available for this project
        - metricCount: Total number of available metrics
        
    Example:
        # List all metrics for a project
        result = await list_available_metrics(project_name="my-project")
        
        # Response format:
        {
            "status": "success",
            "projectName": "my-project",
            "availableMetrics": ["CPU", "Memory", "Availability", "Latency", ...],
            "metricCount": 15
        }
    """
    try:
        # Get current API client
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        # Validate input
        if not project_name:
            return {
                "status": "error",
                "message": "project_name is a required parameter"
            }
        
        logger.info(f"Fetching available metrics for project={project_name}")
        
        # Fetch metric metadata using the API client
        result = await api_client.get_metric_metadata(project_name=project_name)
        
        if result.get("status") == "error":
            return result
        
        # Extract and structure the response
        raw_data = result.get("data", {})
        metric_list = raw_data.get("possibleMetricList", [])
        print(metric_list)
        
        return {
            "status": "success",
            "projectName": project_name,
            "availableMetrics": metric_list,
            "metricCount": len(metric_list)
        }
        
    except Exception as e:
        logger.error(f"Error fetching metric metadata: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch metric metadata: {str(e)}"
        }


@mcp_server.tool()
async def validate_instance_name(
    project_name: str,
    instance_name: str
) -> Dict[str, Any]:
    """
    Validate if an instance name exists in a project.
    
    This tool checks if the provided instance_name is available in the specified project
    by querying the list of available instances. Use this before calling get_metric_data
    to ensure the instance exists and avoid errors.
    
    **When to use this tool:**
    - Before calling get_metric_data to validate the instance_name
    - When user specifies an instance and you want to confirm it exists
    - To get helpful error messages with available instances if validation fails
    
    **Workflow:**
    1. User specifies project_name and instance_name
    2. Call this tool to validate the instance
    3. If valid, proceed with get_metric_data
    4. If invalid, the response includes available instances to choose from
    
    Args:
        project_name: Name of the project to check (required)
        instance_name: Name of the instance to validate (required)
        
    Returns:
        A dictionary containing:
        - status: "success" (if valid) or "error" (if invalid)
        - valid: Boolean indicating if the instance is valid
        - projectName: Name of the queried project
        - instanceName: The instance name that was validated
        - availableInstances: List of available instances (if invalid, first 50 shown)
        - totalAvailableInstances: Total count of available instances
        
    Example:
        # Validate an instance before getting metric data
        result = await validate_instance_name(
            project_name="my-project",
            instance_name="server-01"
        )
        
        # If valid:
        {
            "status": "success",
            "valid": true,
            "projectName": "my-project",
            "instanceName": "server-01",
            "message": "Instance 'server-01' is valid for project 'my-project'"
        }
        
        # If invalid:
        {
            "status": "error",
            "valid": false,
            "projectName": "my-project",
            "instanceName": "invalid-server",
            "availableInstances": ["server-01", "server-02", ...],
            "totalAvailableInstances": 25,
            "message": "Instance 'invalid-server' not found in project 'my-project'"
        }
    """
    try:
        # Validate inputs
        if not project_name:
            return {
                "status": "error",
                "message": "project_name is a required parameter"
            }
        
        if not instance_name:
            return {
                "status": "error",
                "message": "instance_name is a required parameter"
            }
        
        logger.info(f"Validating instance_name='{instance_name}' for project='{project_name}'")
        
        # Use list_available_instances_for_project to get all instances
        # Import the function from system_info_tools
        from .system_info_tools import list_available_instances_for_project
        
        instances_result = await list_available_instances_for_project(
            project_name=project_name,
            page=1,
            page_size=5000  # Get a large page to check all instances
        )
        
        if instances_result.get("status") == "error":
            return instances_result
        
        available_instances = instances_result.get("availableInstances", [])
        total_count = instances_result.get("pagination", {}).get("totalCount", 0)
        actual_project_name = instances_result.get("projectName", project_name)
        
        # Check if the instance exists
        if instance_name in available_instances:
            return {
                "status": "success",
                "valid": True,
                "projectName": actual_project_name,
                "instanceName": instance_name,
                "message": f"Instance '{instance_name}' is valid for project '{actual_project_name}'"
            }
        else:
            return {
                "status": "error",
                "valid": False,
                "projectName": actual_project_name,
                "instanceName": instance_name,
                "availableInstances": available_instances[:50],  # Show first 50
                "totalAvailableInstances": total_count,
                "message": f"Instance '{instance_name}' not found in project '{actual_project_name}'. Please use one of the available instances or call list_available_instances_for_project to see all {total_count} instances.",
                "hint": "Use list_available_instances_for_project tool to see all available instances"
            }
        
    except Exception as e:
        logger.error(f"Error validating instance: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to validate instance: {str(e)}"
        }


@mcp_server.tool()
async def validate_metric_name(
    project_name: str,
    metric_list: List[str]
) -> Dict[str, Any]:
    """
    Validate if metric names exist in a project.
    
    This tool checks if all provided metric names are available in the specified project
    by querying the list of available metrics. Use this before calling get_metric_data
    to ensure the metrics exist and avoid errors.
    
    **When to use this tool:**
    - Before calling get_metric_data to validate the metric_list
    - When user specifies metrics and you want to confirm they exist
    - To get helpful error messages with available metrics if validation fails
    
    **Workflow:**
    1. User specifies project_name and metric_list
    2. Call this tool to validate the metrics
    3. If all valid, proceed with get_metric_data
    4. If any invalid, the response includes available metrics to choose from
    
    Args:
        project_name: Name of the project to check (required)
        metric_list: List of metric names to validate (required)
        
    Returns:
        A dictionary containing:
        - status: "success" (if all valid) or "error" (if any invalid)
        - valid: Boolean indicating if all metrics are valid
        - projectName: Name of the queried project
        - requestedMetrics: The metrics that were validated
        - validMetrics: List of metrics that are valid
        - invalidMetrics: List of metrics that are invalid (if any)
        - availableMetrics: List of available metrics (if any invalid, first 20 shown)
        - totalAvailableMetrics: Total count of available metrics
        
    Example:
        # Validate metrics before getting metric data
        result = await validate_metric_name(
            project_name="my-project",
            metric_list=["CPU", "Memory", "Availability"]
        )
        
        # If all valid:
        {
            "status": "success",
            "valid": true,
            "projectName": "my-project",
            "requestedMetrics": ["CPU", "Memory", "Availability"],
            "validMetrics": ["CPU", "Memory", "Availability"],
            "message": "All 3 metrics are valid for project 'my-project'"
        }
        
        # If some invalid:
        {
            "status": "error",
            "valid": false,
            "projectName": "my-project",
            "requestedMetrics": ["CPU", "InvalidMetric"],
            "validMetrics": ["CPU"],
            "invalidMetrics": ["InvalidMetric"],
            "availableMetrics": ["CPU", "Memory", "Availability", ...],
            "totalAvailableMetrics": 15,
            "message": "1 invalid metric(s) found: ['InvalidMetric']"
        }
    """
    try:
        # Validate inputs
        if not project_name:
            return {
                "status": "error",
                "message": "project_name is a required parameter"
            }
        
        if not metric_list or len(metric_list) == 0:
            return {
                "status": "error",
                "message": "metric_list is required and must contain at least one metric name"
            }
        
        logger.info(f"Validating metric_list={metric_list} for project='{project_name}'")
        
        # Use list_available_metrics to get all available metrics
        metrics_result = await list_available_metrics(project_name=project_name)
        
        if metrics_result.get("status") == "error":
            return metrics_result
        
        available_metrics = metrics_result.get("availableMetrics", [])
        total_count = metrics_result.get("metricCount", 0)
        actual_project_name = metrics_result.get("projectName", project_name)
        
        # Check which metrics are valid and which are invalid
        valid_metrics = [m for m in metric_list if m in available_metrics]
        invalid_metrics = [m for m in metric_list if m not in available_metrics]
        
        # All metrics are valid
        if not invalid_metrics:
            return {
                "status": "success",
                "valid": True,
                "projectName": actual_project_name,
                "requestedMetrics": metric_list,
                "validMetrics": valid_metrics,
                "message": f"All {len(metric_list)} metric(s) are valid for project '{actual_project_name}'"
            }
        else:
            return {
                "status": "error",
                "valid": False,
                "projectName": actual_project_name,
                "requestedMetrics": metric_list,
                "validMetrics": valid_metrics,
                "invalidMetrics": invalid_metrics,
                "availableMetrics": available_metrics[:20],  # Show first 20
                "totalAvailableMetrics": total_count,
                "message": f"{len(invalid_metrics)} invalid metric(s) found: {invalid_metrics}. Please use valid metrics or call list_available_metrics to see all {total_count} available metrics.",
                "hint": "Use list_available_metrics tool to see all available metrics"
            }
        
    except Exception as e:
        logger.error(f"Error validating metrics: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to validate metrics: {str(e)}"
        }
