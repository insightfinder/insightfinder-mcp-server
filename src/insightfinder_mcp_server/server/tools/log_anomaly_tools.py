import sys
import json
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    format_timestamp_in_user_timezone,
    format_api_timestamp_corrected,
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

# Layer 0: Ultra-compact log anomaly overview (just counts and basic info)
@mcp_server.tool()
async def get_log_anomalies_overview(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of log anomalies - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration.
    Use this tool when a user first asks about log anomalies to get a quick overview.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

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
        
        # Basic counts and metrics
        total_anomalies = len(log_anomalies)
        
        # Severity analysis based on anomaly scores
        high_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) > 100])
        medium_severity = len([la for la in log_anomalies if 10 <= la.get("anomalyScore", 0) <= 100])
        low_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) < 10])
        
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
                "severity_distribution": {
                    "high": high_severity,
                    "medium": medium_severity,
                    "low": low_severity
                },
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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 10,
    min_severity: str = "low",
    project_name: Optional[str] = None,
    include_raw_data: bool = False
) -> Dict[str, Any]:
    """
    Fetches a detailed list of log anomalies with comprehensive information.
    This is the main tool for getting log anomaly details - combines basic info with parsed raw data.
    
    The response includes parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.) and formatted summaries.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
        limit (int): Maximum number of anomalies to return (default: 10).
        min_severity (str): Minimum severity level - "low" (>0), "medium" (>10), "high" (>100).
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
        include_raw_data (bool): Whether to include raw log data (default: False for performance).
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

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
        
        # Filter by severity
        severity_thresholds = {"low": 0, "medium": 10, "high": 100}
        min_score = severity_thresholds.get(min_severity, 0)
        log_anomalies = [la for la in log_anomalies if la.get("anomalyScore", 0) >= min_score]
        
        # Sort by anomaly score (highest first) and limit
        log_anomalies = sorted(log_anomalies, key=lambda x: x.get("anomalyScore", 0), reverse=True)[:limit]

        # Create detailed anomaly list
        anomaly_list = []
        for i, anomaly in enumerate(log_anomalies):
            # Determine severity category
            score = anomaly.get("anomalyScore", 0)
            if score > 100:
                severity = "high"
            elif score >= 10:
                severity = "medium"
            else:
                severity = "low"
                
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
                "severity": severity,
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
                
                # Always provide a readable summary
                if parsed_data and isinstance(parsed_data, dict):
                    # Create a nice formatted summary
                    summary_parts = []
                    for key, value in list(parsed_data.items())[:8]:  # Limit to first 8 fields for list view
                        summary_parts.append(f"{key}: {value}")
                    anomaly_info["raw_data_summary"] = " | ".join(summary_parts)
                    if len(parsed_data) > 8:
                        anomaly_info["raw_data_summary"] += f" | ... ({len(parsed_data) - 8} more fields)"
                else:
                    # Fallback to string representation
                    raw_data_str = str(raw_data)
                    anomaly_info["raw_data_summary"] = raw_data_str[:200] + "..." if len(raw_data_str) > 200 else raw_data_str
                    
                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "min_severity": min_severity,
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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Provides statistical analysis of log anomalies for a system over a time period.
    Use this to understand anomaly patterns, frequency, and impact.

    Args:
        system_name (str): The name of the system to analyze.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

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

        # Calculate statistics
        total_anomalies = len(log_anomalies)
        
        # Severity distribution
        high_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) > 100])
        medium_severity = len([la for la in log_anomalies if 10 <= la.get("anomalyScore", 0) <= 100])
        low_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) < 10])
        
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
                "severity_distribution": {
                    "high": high_severity,
                    "medium": medium_severity,
                    "low": low_severity
                },
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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 20,
    include_raw_data: bool = True
) -> Dict[str, Any]:
    """
    Fetches log anomalies specifically for a given project within a system.
    This function includes detailed raw data and comprehensive information for each anomaly.
    Use this tool when the user specifies both a system name and project name.
    
    The response includes full anomaly details with:
    - Basic anomaly information (timestamp, component, instance, pattern, zone, score, severity)
    - Parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.)
    - Key fields extraction for common data elements (including 'cdn' if available)
    - Formatted summaries for better readability
    - JSON parsing for structured data display
    
    Note for LLM: When presenting anomaly details, always list the 'cdn' field if it is present in the data.
    
    Example usage:
    - "show me log anomalies for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get log anomalies before incident for project X in system Y"

    Args:
        system_name (str): The name of the system (e.g., "InsightFinder Demo System (APP)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time_ms (int): Start time in milliseconds (owner timezone)
        end_time_ms (int): End time in milliseconds (owner timezone)
        limit (int): Maximum number of anomalies to return (default: 20)
        include_raw_data (bool): Whether to include full raw data details (default: True)
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with ONLY the system name
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,  # Use only the system name here
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by the specific project name
        # project_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
        project_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # Sort by timestamp (most recent first) and limit
        project_anomalies = sorted(project_anomalies, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]

        # Create detailed anomaly list for the project
        anomaly_list = []
        for i, anomaly in enumerate(project_anomalies):
            # Determine severity category
            score = anomaly.get("anomalyScore", 0)
            if score > 100:
                severity = "high"
            elif score >= 10:
                severity = "medium"
            else:
                severity = "low"
                
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
                "severity": severity,
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
                
                # Always provide a readable summary
                if parsed_data and isinstance(parsed_data, dict):
                    # Create a nice formatted summary
                    summary_parts = []
                    for key, value in list(parsed_data.items())[:10]:  # Limit to first 10 fields
                        summary_parts.append(f"{key}: {value}")
                    anomaly_info["raw_data_summary"] = " | ".join(summary_parts)
                    if len(parsed_data) > 10:
                        anomaly_info["raw_data_summary"] += f" | ... ({len(parsed_data) - 10} more fields)"
                else:
                    # Fallback to string representation
                    raw_data_str = str(raw_data)
                    anomaly_info["raw_data_summary"] = raw_data_str[:300] + "..." if len(raw_data_str) > 300 else raw_data_str
                    
                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "query_type": "project_specific_log_anomalies",
            "system_name": system_name,
            "project_name": project_name,
            "include_raw_data": include_raw_data,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "total_system_anomalies": len(log_anomalies),
            "project_anomalies_found": len(project_anomalies),
            "returned_count": len(anomaly_list),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_project_log_anomalies: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}
