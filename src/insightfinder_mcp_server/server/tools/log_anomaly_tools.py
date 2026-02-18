import sys
import json
from typing import Dict, Any, Optional, List, Union, Union
from datetime import datetime, timezone

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    format_timestamp_in_user_timezone,
    format_api_timestamp_corrected,
    convert_to_ms,
    parse_time_parameters,
)

def _get_api_client():
    """
    Get the API client for the current request context.
    
    Returns:
        InsightFinderAPIClient: The API client configured for the current request
        
    Raises:
        ValueError: If no API client is available (missing headers or not in HTTP context)
    """
    api_client = get_current_api_client()
    if not api_client:
        raise ValueError(
            "InsightFinder API client not available. "
            "This tool requires InsightFinder credentials in HTTP headers: "
            "X-InsightFinder-License-Key and X-InsightFinder-User-Name"
        )
    return api_client

def _matches_instance_name(api_instance_name: str, provided_instance_name: str) -> bool:
    """
    Check if the provided instance name matches the API instance name.
    
    Handles both exact matches and partial matches after underscore:
    - "insightfinder-generallogworker-0" matches "insightfinder-generallogworker-0"
    - "insightfinder-generallogworker-0" matches "generallogworker-app_insightfinder-generallogworker-0" (matches part after _)
    
    Args:
        api_instance_name: The instance name returned by the API (e.g., "generallogworker-app_insightfinder-generallogworker-0")
        provided_instance_name: The instance name provided by the user (e.g., "insightfinder-generallogworker-0")
        
    Returns:
        bool: True if the names match (either exactly or after underscore)
    """
    api_name_lower = api_instance_name.lower()
    provided_name_lower = provided_instance_name.lower()
    
    # Case 1: Exact match
    if api_name_lower == provided_name_lower:
        return True
    
    # Case 2: Match the part after underscore
    if "_" in api_name_lower:
        # Extract the part after the last underscore
        part_after_underscore = api_name_lower.split("_")[-1]
        if part_after_underscore == provided_name_lower:
            return True
    
    # Case 3: Check if provided name is in the full API name (loose matching)
    # This handles cases like user providing "generallogworker" should match "generallogworker-app_..."
    if provided_name_lower in api_name_lower:
        return True
    
    return False

# Layer 0: Ultra-compact log anomaly overview (just counts and basic info)
@mcp_server.tool()
async def get_log_anomalies_overview(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of log anomalies - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration.
    Use this tool when a user first asks about log anomalies to get a quick overview.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time (Optional[Union[str, int]]): The start of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
            If not provided, defaults to 24 hours ago.
        end_time (Optional[Union[str, int]]): The end of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
            If not provided, defaults to the current time.
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms = convert_to_ms(start_time, "start_time", tz_name)
            end_time_ms = convert_to_ms(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            # log_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]
        
        # Basic counts and metrics
        total_anomalies = len(log_anomalies)
        
        # Time range analysis
        if log_anomalies:
            timestamps = [anomaly["timestamp"] for anomaly in log_anomalies]
            first_anomaly = min(timestamps)
            last_anomaly = max(timestamps)
        else:
            first_anomaly = last_anomaly = None

        # Component and instance analysis (just unique counts)
        unique_components = len(set(anomaly.get("componentName", "Unknown") for anomaly in log_anomalies))
        unique_instances = len(set(anomaly.get("instanceName", "Unknown") for anomaly in log_anomalies))
        unique_patterns = len(set(anomaly.get("patternName", "Unknown") for anomaly in log_anomalies))
        unique_projects = len(set(anomaly.get("projectDisplayName", "Unknown") for anomaly in log_anomalies))
        unique_zones = len(set(anomaly.get("zoneName", "Unknown") for anomaly in log_anomalies if anomaly.get("zoneName")))

        # Anomaly score statistics
        if log_anomalies:
            scores = [anomaly.get("anomalyScore", 0) for anomaly in log_anomalies]
            max_score = max(scores)
            min_score = min(scores)
            avg_score = sum(scores) / len(scores)
        else:
            max_score = min_score = avg_score = 0

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "summary": {
                "total_anomalies": total_anomalies,
                "unique_components": unique_components,
                "unique_instances": unique_instances,
                "unique_patterns": unique_patterns,
                "unique_projects": unique_projects,
                "unique_zones": unique_zones,
                "score_statistics": {
                    "max_score": round(max_score, 2),
                    "min_score": round(min_score, 2),
                    "avg_score": round(avg_score, 2)
                },
                "first_anomaly": format_api_timestamp_corrected(first_anomaly, tz_name) if first_anomaly else None,
                "last_anomaly": format_api_timestamp_corrected(last_anomaly, tz_name) if last_anomaly else None,
                "has_anomalies": total_anomalies > 0
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_overview: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 1: Enhanced log anomaly list with detailed information
@mcp_server.tool()
async def get_log_anomalies_list(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 10,
    project_name: Optional[str] = None,
    include_raw_data: bool = False
) -> Dict[str, Any]:
    """
    Fetches a detailed list of log anomalies with comprehensive information.
    This is the main tool for getting log anomaly details - combines basic info with parsed raw data.
    
    The response includes parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.) and formatted summaries.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time (Optional[Union[str, int]]): The start of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): The end of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit (int): Maximum number of anomalies to return (default: 10).
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
        include_raw_data (bool): Whether to include raw log data (default: False for performance).
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms = convert_to_ms(start_time, "start_time", tz_name)
            end_time_ms = convert_to_ms(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            # log_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]

        # Sort by anomaly score (highest first) and limit
        log_anomalies = sorted(log_anomalies, key=lambda x: x.get("anomalyScore", 0), reverse=True)[:limit]

        # Create detailed anomaly list
        anomaly_list = []
        for i, anomaly in enumerate(log_anomalies):                
            anomaly_info = {
                "id": i + 1,
                "timestamp": anomaly["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(anomaly["timestamp"], tz_name),
                "project": anomaly.get("projectDisplayName", "Unknown"),
                "component": anomaly.get("componentName", "Unknown"),
                "instance": anomaly.get("instanceName", "Unknown"),
                "pattern": anomaly.get("patternName", "Unknown"),
                "zone": anomaly.get("zoneName", "Unknown"),
                "anomaly_score": round(anomaly.get("anomalyScore", 0), 2),
                "is_incident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0)
            }
            
            # Add raw data if requested and available
            if "rawData" in anomaly and anomaly["rawData"]:
                raw_data = anomaly["rawData"]
                
                # Parse and format raw data for better display
                parsed_data = None
                raw_data_fields = {}
                
                # Try to parse JSON if it's a string
                if isinstance(raw_data, str):
                    try:
                        parsed_data = json.loads(raw_data)
                        raw_data_fields = parsed_data if isinstance(parsed_data, dict) else {}
                        anomaly_info["raw_data_type"] = "json_string"
                    except json.JSONDecodeError:
                        # Not JSON, treat as plain text
                        raw_data_fields = {"content": raw_data}
                        anomaly_info["raw_data_type"] = "plain_text"
                elif isinstance(raw_data, dict):
                    # Already a dictionary
                    raw_data_fields = raw_data
                    parsed_data = raw_data
                    anomaly_info["raw_data_type"] = "dictionary"
                else:
                    # Other data types
                    raw_data_fields = {"content": str(raw_data)}
                    anomaly_info["raw_data_type"] = "other"
                
                # Add parsed fields for easy access
                if raw_data_fields:
                    anomaly_info["raw_data_fields"] = raw_data_fields
                    
                    # Extract common fields if they exist
                    common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time", "execution_uid"]
                    extracted_fields = {}
                    for field in common_fields:
                        if field in raw_data_fields:
                            print(f"Extracting field '{field}' from raw data: {raw_data_fields[field]}")
                            extracted_fields[field] = raw_data_fields[field]
                    
                    if extracted_fields:
                        anomaly_info["key_fields"] = extracted_fields
                
                # Include full raw data if requested
                if include_raw_data:
                    anomaly_info["raw_data"] = raw_data

                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "limit": limit,
                "project_name": project_name,
                "include_raw_data": include_raw_data
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "total_found": len(result["data"]),
            "returned_count": len(anomaly_list),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_list: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 2: Statistics and analysis tools
@mcp_server.tool()
async def get_log_anomalies_statistics(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Provides comprehensive statistical analysis of log anomalies for a system over a time period.
    Use this tool to understand anomaly patterns, frequency, distribution, and impact across components.
    Ideal for comparing log anomalies between time periods.

    ⚠️ RELATIVE DATE KEYWORDS SUPPORTED:
    You can use simple keywords instead of explicit dates:
    - "thisweek" or "this_week": Monday to today
    - "lastweek" or "last_week": Last Monday to Last Sunday
    - "thismonth" or "this_month": 1st of current month to today
    - "lastmonth" or "last_month": 1st of last month to last day of last month
    - "today": Today's date (full day)
    - "yesterday": Yesterday's date (full day)

    COMPARISON EXAMPLES - Use these keywords directly without calculating dates:
        To compare "This week" vs "Last week":
        - Call 1: start_time="thisweek", end_time="thisweek"
        - Call 2: start_time="lastweek", end_time="lastweek"

        To compare "This month" vs "Last month":
        - Call 1: start_time="thismonth", end_time="thismonth"
        - Call 2: start_time="lastmonth", end_time="lastmonth"

    Args:
        system_name (str): The name of the system to analyze.
        start_time (Optional[Union[str, int]]): The start of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        end_time (Optional[Union[str, int]]): The end of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    
    Returns:
        Statistical breakdown with anomaly counts, score analysis, and top affected components, instances, and projects.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Parse time parameters (supports both keywords and absolute dates)
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]

        # Calculate statistics
        total_anomalies = len(log_anomalies)
        
        # Group by component, instance, pattern, zone, and project
        components = {}
        instances = {}
        patterns = {}
        zones = {}
        projects = {}
        
        for anomaly in log_anomalies:
            # Component analysis
            component = anomaly.get("componentName", "Unknown")
            components[component] = components.get(component, 0) + 1
            
            # Instance analysis
            instance = anomaly.get("instanceName", "Unknown")
            instances[instance] = instances.get(instance, 0) + 1
            
            # Pattern analysis
            pattern = anomaly.get("patternName", "Unknown")
            patterns[pattern] = patterns.get(pattern, 0) + 1
            
            # Zone analysis
            zone = anomaly.get("zoneName", "Unknown")
            if zone != "Unknown":
                zones[zone] = zones.get(zone, 0) + 1
                
            # Project analysis
            project = anomaly.get("projectDisplayName", "Unknown")
            projects[project] = projects.get(project, 0) + 1

        # Score statistics
        if log_anomalies:
            scores = [anomaly.get("anomalyScore", 0) for anomaly in log_anomalies]
            max_score = max(scores)
            min_score = min(scores)
            avg_score = sum(scores) / len(scores)
        else:
            max_score = min_score = avg_score = 0

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "statistics": {
                "total_anomalies": total_anomalies,
                "score_statistics": {
                    "max_score": round(max_score, 2),
                    "min_score": round(min_score, 2),
                    "avg_score": round(avg_score, 2)
                },
                "top_affected_components": dict(sorted(components.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_instances": dict(sorted(instances.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_patterns": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_projects": dict(sorted(projects.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_zones": dict(sorted(zones.items(), key=lambda x: x[1], reverse=True)[:10]) if zones else {}
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_statistics: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Project-specific query tool
@mcp_server.tool()
async def get_project_log_anomalies(
    system_name: str,
    project_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20,
    offset: int = 0,
    instance_name: Optional[str] = None,
    include_raw_data: bool = True
) -> Dict[str, Any]:
    """
    Fetches log anomalies specifically for a given project within a system with pagination support.
    This function includes detailed raw data and comprehensive information for each anomaly.
    Use this tool when the user specifies both a system name and project name.
    
    The response includes full anomaly details with:
    - Basic anomaly information (timestamp, component, instance, pattern, zone, score)
    - Parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.)
    - Key fields extraction for common data elements (including 'cdn' if available)
    - Formatted summaries for better readability
    - JSON parsing for structured data display
    
    Note for LLM: When presenting anomaly details, always list the 'cdn' field if it is present in the data.
    
    Example usage:
    - "show me log anomalies for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get log anomalies before incident for project X in system Y"
    - "show me next 20 anomalies" (use offset parameter)
    - "show me anomalies for instance instance-1" (use instance_name parameter)

    Args:
        system_name (str): The name of the system (e.g., "InsightFinder Demo System (APP)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit (int): Maximum number of anomalies to return (default: 20)
        offset (int): Number of anomalies to skip for pagination (default: 0)
        instance_name (str): Optional. Filter results by specific instance name.
        include_raw_data (bool): Whether to include full raw data details (default: True)
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms = convert_to_ms(start_time, "start_time", tz_name)
            end_time_ms = convert_to_ms(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Validate timestamps
        current_time_ms = int(datetime.now().timestamp() * 1000)
        two_days_ms = 2 * 24 * 60 * 60 * 1000
        
        # Check for future timestamps > 2 days
        if start_time_ms > current_time_ms + two_days_ms or end_time_ms > current_time_ms + two_days_ms:
            return {
                "status": "error",
                "message": "Timestamps cannot be more than 2 days in the future."
            }

        print(f"Fetching loganomaly data for {system_name}...", file=sys.stderr)

        # Validate timestamps
        current_time_ms = int(datetime.now().timestamp() * 1000)
        two_days_ms = 2 * 24 * 60 * 60 * 1000
        
        # Check for future timestamps > 2 days
        if start_time_ms > current_time_ms + two_days_ms or end_time_ms > current_time_ms + two_days_ms:
            return {
                "status": "error",
                "message": "Timestamps cannot be more than 2 days in the future."
            }
            
        # Handle same start/end time - expand to full day
        if start_time_ms == end_time_ms:
            # Create a datetime object from the timestamp (assuming UTC)
            dt = datetime.fromtimestamp(start_time_ms / 1000)
            
            # Set to beginning of day (00:00:00)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            start_time_ms = int(start_dt.timestamp() * 1000)
            
            # Set to end of day (23:59:59)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            end_time_ms = int(end_dt.timestamp() * 1000)

        print(f"Fetching loganomaly data for {system_name}...", file=sys.stderr)

        # Call the InsightFinder API client with ONLY the system name
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,  # Use only the system name here
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        print(f"API call completed for {system_name}. Status: {result.get('status', 'unknown')}", file=sys.stderr)

        if result["status"] != "success":
            error_message = f"API error for {system_name}: {result.get('message', 'Unknown error')}"
            print(error_message, file=sys.stderr)
            return result
        log_anomalies = result["data"]
        print(f"Retrieved {len(log_anomalies)} log anomalies for {system_name}", file=sys.stderr)

        # Filter by the specific project name
        # project_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
        project_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # Filter by instance name if provided (with smart matching for different formats)
        if instance_name:
            project_anomalies = [
                la for la in project_anomalies 
                if _matches_instance_name(la.get("instanceName", ""), instance_name)
            ]
            # if settings.ENABLE_DEBUG_MESSAGES:
            #     print(f"[Instance filter] Filtered by instance_name='{instance_name}', remaining: {len(project_anomalies)}", file=sys.stderr)

        # Always only return anomalies of type "whiteList" for project-specific queries
        project_anomalies = [la for la in project_anomalies if str(la.get("type", "")).lower() == "whitelist"]

        # Sort by timestamp (most recent first)
        project_anomalies = sorted(project_anomalies, key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Calculate total count before pagination
        total_project_anomalies = len(project_anomalies)
        
        # Apply pagination
        paginated_anomalies = project_anomalies[offset : offset + limit]
        has_more = (offset + limit) < total_project_anomalies

        # Create detailed anomaly list for the project
        anomaly_list = []
        for i, anomaly in enumerate(paginated_anomalies):                
            anomaly_info = {
                "id": offset + i + 1,  # Global ID based on offset
                "timestamp": anomaly["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(anomaly["timestamp"], tz_name),
                "project": anomaly.get("projectDisplayName", "Unknown"),
                "component": anomaly.get("componentName", "Unknown"),
                "instance": anomaly.get("instanceName", "Unknown"),
                "pattern": anomaly.get("patternName", "Unknown"),
                "zone": anomaly.get("zoneName", "Unknown"),
                "anomaly_score": round(anomaly.get("anomalyScore", 0), 2),
                "is_incident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0)
            }
            
            # Add raw data details if available
            if "rawData" in anomaly and anomaly["rawData"]:
                raw_data = anomaly["rawData"]
                
                # Parse and format raw data for better display
                parsed_data = None
                raw_data_fields = {}
                
                # Try to parse JSON if it's a string
                if isinstance(raw_data, str):
                    try:
                        parsed_data = json.loads(raw_data)
                        raw_data_fields = parsed_data if isinstance(parsed_data, dict) else {}
                        anomaly_info["raw_data_type"] = "json_string"
                    except json.JSONDecodeError:
                        # Not JSON, treat as plain text
                        raw_data_fields = {"content": raw_data}
                        anomaly_info["raw_data_type"] = "plain_text"
                elif isinstance(raw_data, dict):
                    # Already a dictionary
                    raw_data_fields = raw_data
                    parsed_data = raw_data
                    anomaly_info["raw_data_type"] = "dictionary"
                else:
                    # Other data types
                    raw_data_fields = {"content": str(raw_data)}
                    anomaly_info["raw_data_type"] = "other"
                
                # Add parsed fields for easy access
                if raw_data_fields:
                    anomaly_info["raw_data_fields"] = raw_data_fields
                    
                    # Extract common fields if they exist
                    common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time", "execution_uid"]
                    extracted_fields = {}
                    for field in common_fields:
                        if field in raw_data_fields:
                            extracted_fields[field] = raw_data_fields[field]
                    
                    if extracted_fields:
                        anomaly_info["key_fields"] = extracted_fields
                
                # Include full raw data if requested
                if include_raw_data:
                    anomaly_info["raw_data"] = raw_data
                                    
                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "query_type": "project_specific_log_anomalies",
            "system_name": system_name,
            "project_name": project_name,
            "instance_filter": instance_name,
            "include_raw_data": include_raw_data,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "total_available": total_project_anomalies,
                "returned_count": len(anomaly_list),
                "has_more": has_more
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "total_system_anomalies": len(log_anomalies),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_project_log_anomalies: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}
