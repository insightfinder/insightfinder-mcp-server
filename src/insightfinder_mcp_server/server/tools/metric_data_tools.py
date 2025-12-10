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
    end_time_ms: Optional[int] = None,
    sampling_interval: Optional[int] = None
) -> Dict[str, Any]:
    """
    Fetch metric line chart data for specified metrics and instance.
    
    This tool retrieves time-series metric data points for visualization and analysis.
    You can query multiple metrics at once for a specific instance within a project.
    The data includes timestamps and metric values for creating line charts and trend analysis.
    
    **When to use this tool:**
    - When user wants to see metric trends over time
    - To visualize metric performance and patterns
    - To analyze historical metric values for a specific instance
    - To compare multiple metrics side-by-side
    
    **Sampling:** 
    By default, all data points are returned. For large time ranges, the response can be 
    very long. Use the sampling_interval parameter to reduce data points:
    - sampling_interval=2: Returns every 2nd data point
    - sampling_interval=5: Returns every 5th data point
    - sampling_interval=10: Returns every 10th data point
    
    Always confirm with the user before applying sampling, as it reduces granularity.
    
    Args:
        project_name: Name of the project to query (required)
        instance_name: Name of the specific instance/host to query (required)
        metric_list: List of metric names to fetch data for (e.g., ["Availability", "CPU", "Memory"])
        start_time_ms: Start timestamp in milliseconds (13-digit)
        end_time_ms: End timestamp in milliseconds (13-digit)
        sampling_interval: Sample every Nth data point to reduce response size (optional, default: 1 = all points)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - data: List of metric data objects, each containing:
            - metricName: Name of the metric
            - metricData: List of {timestamp, metricValue} data points
            - dataPointCount: Number of data points returned
            - samplingApplied: Whether sampling was applied
            - originalDataPointCount: Original count before sampling (if sampling applied)
        - metadata: Query parameters and time range information
        
    Example:
        # Get CPU and Memory metrics for the last 24 hours
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["CPU", "Memory"]
        )
        
        # Get Availability metric with sampling for last 7 days
        result = await get_metric_data(
            project_name="my-project",
            instance_name="server-01",
            metric_list=["Availability"],
            start_time_ms=start_timestamp,
            end_time_ms=end_timestamp,
            sampling_interval=5  # Get every 5th data point
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
        
        # Validate sampling_interval
        if sampling_interval is not None and sampling_interval < 1:
            return {
                "status": "error",
                "message": "sampling_interval must be a positive integer (1 or greater)"
            }
        
        # Set default sampling to 1 (all points)
        if sampling_interval is None:
            sampling_interval = 1
        
        logger.info(f"Fetching metric data for project={project_name}, instance={instance_name}, "
                   f"metrics={metric_list}, sampling={sampling_interval}")
        
        # Fetch metric data using the API client
        result = await api_client.get_metric_data(
            project_name=project_name,
            instance_name=instance_name,
            metric_list=metric_list,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if result.get("status") == "error":
            return result
        
        # Process and structure the response
        raw_data = result.get("data", [])
        processed_metrics = []
        
        for metric_obj in raw_data:
            metric_name = metric_obj.get("metricName", "Unknown")
            metric_data = metric_obj.get("metricData", [])
            
            # Apply sampling if requested
            original_count = len(metric_data)
            sampled_data = metric_data[::sampling_interval] if sampling_interval > 1 else metric_data
            
            processed_metric = {
                "metricName": metric_name,
                "metricData": sampled_data,
                "dataPointCount": len(sampled_data),
                "samplingApplied": sampling_interval > 1,
            }
            
            if sampling_interval > 1:
                processed_metric["originalDataPointCount"] = original_count
                processed_metric["samplingInterval"] = sampling_interval
            
            processed_metrics.append(processed_metric)
        
        # Format timestamps for display
        start_time_formatted = format_timestamp_in_user_timezone(start_time_ms)
        end_time_formatted = format_timestamp_in_user_timezone(end_time_ms)
        
        return {
            "status": "success",
            "data": processed_metrics,
            "metadata": {
                "projectName": project_name,
                "instanceName": instance_name,
                "requestedMetrics": metric_list,
                "metricsReturned": len(processed_metrics),
                "timeRange": {
                    "startTime": start_time_ms,
                    "endTime": end_time_ms,
                    "startTimeFormatted": start_time_formatted,
                    "endTimeFormatted": end_time_formatted
                },
                "samplingApplied": sampling_interval > 1,
                "samplingInterval": sampling_interval if sampling_interval > 1 else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching metric data: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch metric data: {str(e)}"
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
