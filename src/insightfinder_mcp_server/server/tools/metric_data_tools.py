"""
Metric data tools for the InsightFinder MCP server.

This module provides tools for fetching and analyzing metric time-series data:
- get_metric_data: Fetch metric line chart UI URL for specified metrics and instances
- list_available_metrics: List all available metrics for a project

These tools help users visualize and analyze metric trends, patterns, and historical data points.
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
async def get_metric_data_with_single_metric_name(
    project_name: str,
    instance_name: str,
    metric_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get UI URL for fetching metric line chart data for a single metric.
    This is a convenience wrapper around get_metric_data that accepts a single metric name string
    instead of a list of metrics.

    **CRITICAL WORKFLOW - Follow this exact sequence:**
    
    **For Incident-Related Metrics:**
    1. Get incident details first (get_incidents_list or get_incident_details) to obtain instance_name and metric_name
    2. Call validate_instance_and_metrics(project_name, instance_name, [metric_name]) 
    3. If validation passes (valid=True), IMMEDIATELY call this function - SKIP all other validation steps
    
    **For General Metric Queries:**
    1. If you know instance_name and metric name: Call validate_instance_and_metrics FIRST
    2. If validation passes (valid=True), IMMEDIATELY call this function - SKIP all other validation steps
    3. If validation fails OR you don't know instance/metrics: Use list_available_instances_for_project or list_available_metrics
    
    **Time Range:**
    - Accepts ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
    - start_time must be < end_time (cannot be equal)
    
    Args:
        project_name: Project name (required)
        instance_name: Instance/host name (required)
        metric_name: Single metric name (required, e.g., "CPU")
        start_time: Start time in ISO 8601 format (optional, defaults to 1 day ago)
        end_time: End time in ISO 8601 format (optional, defaults to now)

    Returns:
        - status: "success" or "error"
        - ui-url: Direct URL to InsightFinder UI visualization
        - metadata: Query parameters and time range
        
    Example:
        # Validate first, then fetch (ALWAYS use this pattern)
        validation = await validate_instance_and_metrics(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU"]
        )
        
        # If validation passed, get metric data
        if validation.get("valid"):
            result = await get_metric_data_with_metric_name(
                project_name="my-project",
                instance_name="server-01",
                metric_name="CPU"
            )
    """
    try:
        # Validate that metric_name is provided
        if not metric_name or not isinstance(metric_name, str):
            return {
                "status": "error",
                "message": "metric_name must be a non-empty string"
            }
        
        # Convert single metric name to list and call get_metric_data
        return await get_metric_data(
            project_name=project_name,
            instance_name=instance_name,
            metric_list=[metric_name],
            start_time=start_time,
            end_time=end_time
        )
        
    except Exception as e:
        logger.error(f"Error in get_metric_data_with_metric_name: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Error fetching metric data: {str(e)}"
        }


@mcp_server.tool()
async def get_metric_data(
    project_name: str,
    instance_name: str,
    metric_list: List[str],
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get UI URL for fetching metric line chart data. Returns a URL that users can click
    to visualize metric data in the InsightFinder UI.

    **CRITICAL WORKFLOW - Follow this exact sequence:**
    
    **For Incident-Related Metrics:**
    1. Get incident details first (get_incidents_list or get_incident_details) to obtain instance_name and metric_name
    2. Call validate_instance_and_metrics(project_name, instance_name, metric_list) 
    3. If validation passes (valid=True), IMMEDIATELY call get_metric_data - SKIP all other validation steps
    
    **For General Metric Queries:**
    1. If you know instance_name and metric names: Call validate_instance_and_metrics FIRST
    2. If validation passes (valid=True), IMMEDIATELY call get_metric_data - SKIP all other validation steps
    3. If validation fails OR you don't know instance/metrics: Use list_available_instances_for_project or list_available_metrics
    
    **Time Range:**
    - Accepts ISO 8601 format (e.g., "2026-01-08T21:45:30Z")
    - start_time must be < end_time (cannot be equal)
    
    Args:
        project_name: Project name (required)
        instance_name: Instance/host name (required)
        metric_list: List of metric names (required, e.g., ["CPU", "Memory"])
        start_time: Start time in ISO 8601 format (optional, defaults to 1 day ago)
        end_time: End time in ISO 8601 format (optional, defaults to now)

    Returns:
        - status: "success" or "error"
        - ui-url: Direct URL to InsightFinder UI visualization
        - metadata: Query parameters and time range
        
    Example:
        # Validate first, then fetch (ALWAYS use this pattern)
        validation = await validate_instance_and_metrics(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
        
        # If validation passed, get metric data
        if validation.get("valid"):
            # Validation passed - call get_metric_data immediately
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
        
        customer_name, actual_project_name, display_project_name, instance_list, system_id = project_info
        
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
        
        # Format timestamps for display
        start_time_formatted = format_timestamp_in_user_timezone(start_time_ms)
        end_time_formatted = format_timestamp_in_user_timezone(end_time_ms)

        # Get base URL from API client
        base_api_url = api_client.base_url
        
        # URL encode parameters to handle spaces and special characters
        encoded_system_id = quote(system_id, safe='')
        encoded_customer_name = quote(customer_name, safe='')
        encoded_username = quote(api_client.user_name, safe='')
        encoded_project_name = quote(actual_project_name, safe='')
        encoded_display_project_name = quote(display_project_name, safe='')
        encoded_instance_name = quote(instance_name, safe='')
        encoded_metrics = quote(','.join(metric_list), safe='')
        
        # When the logged-in username matches the customer name, the UI expects projectName without the @customer suffix.
        # Otherwise include the customer (projectName=project@customer)
        if encoded_username == encoded_customer_name:
            project_name_param = f"projectName={encoded_project_name}"
        else:
            project_name_param = f"projectName={encoded_project_name}@{encoded_customer_name}"

        ui_url = (
            f"{base_api_url}/ui/metric/linecharts?e=All&s={encoded_system_id}"
            f"&customerName={encoded_customer_name}&{project_name_param}"
            f"&startTimestamp={start_time_ms}&endTimestamp={end_time_ms}&justSelectMetric={encoded_metrics}"
            f"&sessionMetric=&justInstanceList={encoded_instance_name}&withBaseline=true&incidentInfo=&sourceInfo=&metricAnomalyMap="
            f"&projectDisplayName={encoded_display_project_name}"
        )

        return {
            "status": "success",
            "ui-url": ui_url,
            "message": "Click the URL to visualize metric data in the InsightFinder UI",
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
        logger.info(f"Parameter types: instance_name type={type(instance_name).__name__}, metric_list type={type(metric_list).__name__}")
        
        # Fix: If metric_list is a string that looks like a JSON array, parse it
        if metric_list and isinstance(metric_list, str):
            import json
            try:
                # Try to parse as JSON array
                parsed_list = json.loads(metric_list)
                if isinstance(parsed_list, list):
                    logger.info(f"Parsed metric_list from string to list: {parsed_list}")
                    metric_list = parsed_list
            except (json.JSONDecodeError, ValueError):
                # Not a JSON string, treat as a single metric name
                logger.info(f"Treating metric_list string as single metric: [{metric_list}]")
                metric_list = [metric_list]
        
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
        
        # Check if only instance_name is provided without metrics
        if instance_name and not metric_list:
            logger.info(f"Instance validated but no metrics provided for project='{project_name}', instance='{instance_name}'")
            return {
                "status": "partial",
                "valid": False,
                "projectName": project_name,
                "message": "Instance name provided but metric_list is missing. Please specify which metrics you want to query.",
                "nextAction": "get_metrics_from_incident_or_ask_user",
                "instruction": "The instance name is provided but no metrics were specified. NEXT STEPS: 1) If this is incident-related: Use get_incident_details or get_incidents_list to get the metric names from incident data, then call this validation again with the metrics. 2) If not incident-related: Ask the user which metrics they want to query OR suggest calling list_available_metrics to see all available options.",
                "instanceName": instance_name
            }
        
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
            
            customer_name, actual_project_name, display_project_name, available_instances, system_id = project_info
            result["projectName"] = actual_project_name
            
            logger.info(f"Available instances for project: {available_instances}")
            logger.info(f"Looking for instance: '{instance_name}' (type: {type(instance_name).__name__})")
            logger.info(f"Instance in list check: {instance_name in available_instances}")
            
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
                customer_name, actual_project_name, display_project_name, available_instances, system_id = project_info
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
            
            logger.info(f"Metric validation results: requested={metric_list}, valid={valid_metrics}, invalid={invalid_metrics}")
            
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
        
        logger.info(f"Final validation check: all_validations_passed={all_validations_passed}, errors={errors}")
        
        if all_validations_passed:
            logger.info(f"Validation passed for project='{project_name}', instance='{instance_name}', metrics={metric_list}")
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
