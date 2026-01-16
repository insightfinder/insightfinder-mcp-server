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

    **TIP: Fetching Metrics Related to Incidents**
    - CRITICAL: If you are trying to fetch metric line chart data related to an incident, you need to first
      obtain the incident details by calling incident-related tools (e.g., get_incidents_list,
      get_incident_details) where you can get the **instance name** and **metric name**.
    - Once you have the instance name and metric name from the incident, use them with this tool
      to view the exact metrics that triggered or are associated with the incident.

    **Important Time Range Requirements:**
    - start_time and end_time accept ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
    - start_time must be LESS than end_time (start time must come before end time)
    - start_time and end_time cannot be the same value
    
    **Instance Validation (IMPORTANT - Follow this workflow):**
    - If you already know the instance_name, validate it FIRST using validate_instance_and_metrics tool
      by passing project_name and instance_name before calling this tool.
    - If validation fails or you don't know the instance name, use list_available_instances_for_project
      to see all available instances for the project.
    - Only call get_metric_data after confirming the instance exists.
    
    **Metric Validation (IMPORTANT - Follow this workflow):**
    - If you already know the metric names, validate them FIRST using validate_instance_and_metrics tool
      by passing project_name and metric_list before calling this tool.
    - If validation fails or you don't know the metric names, use list_available_metrics
      to see all available metrics for the project.
    - Only call get_metric_data after confirming the metrics exist.
    
    **Recommended Workflow:**
    1. Use validate_instance_and_metrics to check if both instance and metrics exist
       OR use list_available_instances_for_project and list_available_metrics to discover them
    2. Call get_metric_data with validated instance and metrics
    
    **When to use this tool:**
    - When user wants to see metric trends over time
    - To get a direct link to metric data JSON
    - To visualize metric performance and patterns
    - To analyze historical metric values for a specific instance
    - To compare multiple metrics side-by-side
    
    Args:
        project_name: Name of the project to query (required)
        instance_name: Name of the specific instance/host to query (required)
                      - Should be validated with validate_instance_name tool first
        metric_list: List of metric names to fetch data for (e.g., ["Availability", "CPU", "Memory"])
                    - Should be validated with validate_metric_name tool first
        start_time: Start timestamp in ISO 8601 format (e.g., "2026-01-08T21:45:30Z") 
        end_time: End timestamp in ISO 8601 format (e.g., "2026-01-08T21:45:30Z")

    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - api-url: Direct API URL to access the metric data JSON (click to view in browser)
        - ui-url: Direct URL to view the metric data in InsightFinder UI
        - metadata: Query parameters and time range information
        
    Example:
        # Recommended workflow with validation
        # Validate both instance and metrics in one call
        validation = await validate_instance_and_metrics(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
        
        # If validation passed, get metric data
        if validation.get("valid"):
            result = await get_metric_data(
                project_name="my-project",
                instance_name="server-01",
                metric_list=["CPU", "Memory"]
            )
        
        # Get Availability metric URL for a specific time range (ISO 8601 format)
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["Availability"],
            start_time="2026-01-08T21:45:30Z",
            end_time="2026-01-09T21:45:30Z"
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
async def validate_instance_and_metrics(
    project_name: str,
    instance_name: Optional[str] = None,
    metric_list: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Validate if instance name and/or metric names exist in a project before calling get_metric_data.
    
    IMPORTANT: For best results, provide both instance_name AND metric_list together.
    
    **Validation Process:**
    1. Validates instance_name if provided
    2. Validates metric_list if provided
    3. Returns validation results for each
    
    **When validation passes:**
    - You MUST immediately call get_metric_data with the validated parameters
    - DO NOT call list_available_instances_for_project or list_available_metrics
    - Proceed directly to get_metric_data to fetch the data
    
    **When validation fails:**
    - Use list_available_instances_for_project to see available instances
    - Use list_available_metrics to see available metrics
    
    Args:
        project_name: Name of the project to check (required)
        instance_name: Name of the instance to validate (optional, but recommended)
        metric_list: List of metric names to validate (optional, but recommended)
        
    Returns:
        - status: "success" or "error"
        - valid: true if all validations passed
        - nextAction: "call_get_metric_data" (if valid) or instructions for fixing issues
        - instanceValidation: Results if instance_name provided
        - metricsValidation: Results if metric_list provided
        
    Example:
        result = await validate_instance_and_metrics(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
        # If result['valid'] == True, immediately call get_metric_data
    """
    try:
        # Validate inputs - require at least one of instance_name or metric_list
        if not project_name:
            return {
                "status": "error",
                "message": "project_name is a required parameter"
            }
        
        if not instance_name and not metric_list:
            return {
                "status": "error",
                "message": "At least one of instance_name or metric_list must be provided. For best results, provide both."
            }
        
        logger.info(f"Validating for project='{project_name}', instance='{instance_name}', metrics={metric_list}")
        
        # Get API client
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        result: Dict[str, Any] = {
            "status": "success",
            "valid": True,
            "projectName": project_name
        }
        
        all_validations_passed = True
        errors: List[str] = []
        
        # STEP 1: Validate instance if provided
        if instance_name:
            logger.info(f"Step 1: Validating instance_name='{instance_name}'")
            
            # Get project info directly from API client (includes full instance list)
            project_info = await api_client.get_customer_name_for_project(project_name)
            if not project_info:
                return {
                    "status": "error",
                    "message": f"Project '{project_name}' not found. Please verify the project name or use list_all_systems_and_projects to see available projects."
                }
            
            customer_name, actual_project_name, available_instances, system_id = project_info
            result["projectName"] = actual_project_name
            
            instance_validation: Dict[str, Any] = {
                "instanceName": instance_name
            }
            
            # Check if the instance exists
            if instance_name not in available_instances:
                instance_validation["valid"] = False
                instance_validation["availableInstances"] = available_instances[:50]  # Show first 50
                instance_validation["totalAvailableInstances"] = len(available_instances)
                errors.append(f"Instance '{instance_name}' not found")
                all_validations_passed = False
            else:
                instance_validation["valid"] = True
            
            result["instanceValidation"] = instance_validation
        
        # STEP 2: Validate metrics if provided
        if metric_list:
            if len(metric_list) == 0:
                return {
                    "status": "error",
                    "message": "metric_list must contain at least one metric name"
                }
            
            logger.info(f"Step 2: Validating metric_list={metric_list}")
            
            # Get actual project name if not already fetched
            if "projectName" not in result or result["projectName"] == project_name:
                project_info = await api_client.get_customer_name_for_project(project_name)
                if not project_info:
                    return {
                        "status": "error",
                        "message": f"Project '{project_name}' not found. Please verify the project name or use list_all_systems_and_projects to see available projects."
                    }
                customer_name, actual_project_name, available_instances, system_id = project_info
                result["projectName"] = actual_project_name
            else:
                actual_project_name = result["projectName"]
            
            # Use list_available_metrics to get all available metrics
            metrics_result = await list_available_metrics(project_name=actual_project_name)
            
            if metrics_result.get("status") == "error":
                return metrics_result
            
            available_metrics = metrics_result.get("availableMetrics", [])
            total_count = metrics_result.get("metricCount", 0)
            
            # Check which metrics are valid and which are invalid
            valid_metrics = [m for m in metric_list if m in available_metrics]
            invalid_metrics = [m for m in metric_list if m not in available_metrics]
            
            metrics_validation: Dict[str, Any] = {
                "requestedMetrics": metric_list,
                "validMetrics": valid_metrics
            }
            
            if invalid_metrics:
                metrics_validation["valid"] = False
                metrics_validation["invalidMetrics"] = invalid_metrics
                metrics_validation["availableMetrics"] = available_metrics[:20]  # Show first 20
                metrics_validation["totalAvailableMetrics"] = total_count
                errors.append(f"{len(invalid_metrics)} invalid metric(s): {invalid_metrics}")
                all_validations_passed = False
            else:
                metrics_validation["valid"] = True
            
            result["metricsValidation"] = metrics_validation
        
        # Set final status and next action
        result["valid"] = all_validations_passed
        
        if all_validations_passed:
            result["status"] = "success"
            result["message"] = "All validations passed"
            result["nextAction"] = "call_get_metric_data"
            result["instruction"] = "Validation successful! Proceed directly to call get_metric_data with these validated parameters. DO NOT call list_available_instances_for_project or list_available_metrics."
        else:
            result["status"] = "error"
            result["message"] = "Validation failed: " + "; ".join(errors)
            
            # Provide specific guidance on what to do next
            next_actions = []
            if instance_name and not result.get("instanceValidation", {}).get("valid"):
                next_actions.append("Call list_available_instances_for_project to see available instances")
            if metric_list and not result.get("metricsValidation", {}).get("valid"):
                next_actions.append("Call list_available_metrics to see available metrics")
            
            if next_actions:
                result["nextAction"] = " OR ".join(next_actions)
        
        return result
        
    except Exception as e:
        logger.error(f"Error validating instance and metrics: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to validate: {str(e)}"
        }
