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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get the API URL for fetching metric line chart data for specified metrics and instance.
    
    This tool validates the request by making an API call to InsightFinder, then returns 
    the API URL that users can click to directly access the JSON metric data in their browser.
    
    **Important Time Range Requirements:**
    - start_time_ms and end_time_ms must be valid timestamps in milliseconds (13-digit epoch time)
    - start_time_ms must be LESS than end_time_ms (start time must come before end time)
    - start_time_ms and end_time_ms cannot be the same value
    
    **When to use this tool:**
    - When user wants to see metric trends over time
    - To get a direct link to metric data JSON
    - To visualize metric performance and patterns
    - To analyze historical metric values for a specific instance
    - To compare multiple metrics side-by-side
    
    Args:
        project_name: Name of the project to query (required)
        instance_name: Name of the specific instance/host to query (required)
        metric_list: List of metric names to fetch data for (e.g., ["Availability", "CPU", "Memory"])
        start_time_ms: Start timestamp in milliseconds (13-digit epoch time, must be before end_time_ms)
        end_time_ms: End timestamp in milliseconds (13-digit epoch time, must be after start_time_ms)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - url: Direct API URL to access the metric data JSON (click to view in browser)
        - metadata: Query parameters and time range information
        
    Example:
        # Get CPU and Memory metrics URL for the last 24 hours
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
        
        # Get Availability metric URL for a specific time range
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["Availability"],
            start_time_ms=1734307200000,  # Must be valid and before end_time_ms
            end_time_ms=1734393600000     # Must be valid and after start_time_ms
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
        
        # Get time range with timezone awareness
        if start_time_ms is None or end_time_ms is None:
            start_time_ms, end_time_ms = get_timezone_aware_time_range_ms(days_back=1)
        
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
        
        logger.info(f"Fetching metric data URL for project={project_name}, instance={instance_name}, "
                   f"metrics={metric_list}")
        
        # Fetch metric data using the API client to validate the request works
        result = await api_client.get_metric_data(
            project_name=project_name,
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
        
        return {
            "status": "success",
            "url": api_url,
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
