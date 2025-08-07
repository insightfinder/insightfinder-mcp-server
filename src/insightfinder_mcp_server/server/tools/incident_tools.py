import sys
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..server import mcp_server
from ...api_client.insightfinder_client import api_client
from ...config.settings import settings
from .get_time import get_timezone_aware_timestamp_ms, get_timezone_aware_time_range_ms, format_timestamp_in_user_timezone, get_today_time_range_ms, format_timestamp_no_conversion, format_api_timestamp_corrected

# Layer 0: Ultra-compact incident overview (just counts and basic info)
@mcp_server.tool()
async def get_incidents_overview(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of incidents - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration.
    Use this tool when a user first asks about incidents to get a quick overview.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
    """
    try:
        # print(f"[DEBUG] get_incidents_overview called with system_name={system_name}, start_time_ms={start_time_ms}, end_time_ms={end_time_ms}", file=sys.stderr)
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        # print(f"[DEBUG] Using time range: {start_time_ms} to {end_time_ms}", file=sys.stderr)
        # Call the InsightFinder API client
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if settings.ENABLE_DEBUG_MESSAGES:
            print(f"[DEBUG] Overview query - TZ env: {os.getenv('TZ', 'Not set')}", file=sys.stderr)
            print(f"[DEBUG] Query range: {start_time_ms} to {end_time_ms}", file=sys.stderr)
            print(f"[DEBUG] Query range formatted: {format_timestamp_in_user_timezone(start_time_ms)} to {format_timestamp_in_user_timezone(end_time_ms)}", file=sys.stderr)
            print(f"[DEBUG] API response status: {result.get('status', 'unknown')}", file=sys.stderr)
            if result.get("status") == "success":
                print(f"[DEBUG] API response data length: {len(result.get('data', []))}", file=sys.stderr)
            else:
                print(f"[DEBUG] API response error: {result.get('message', 'No message')}", file=sys.stderr)

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        if settings.ENABLE_DEBUG_MESSAGES and incidents:
            print(f"[DEBUG] Found {len(incidents)} incidents", file=sys.stderr)
            print(f"[DEBUG] Raw API response sample: {incidents[0] if incidents else 'No incidents'}", file=sys.stderr)
            for i, incident in enumerate(incidents[:3]):  # Show first 3 for debugging
                raw_timestamp = incident["timestamp"]
                timestamp_with_conversion = format_timestamp_in_user_timezone(raw_timestamp)
                timestamp_no_conversion = format_timestamp_no_conversion(raw_timestamp)
                timestamp_corrected = format_api_timestamp_corrected(raw_timestamp)
                print(f"[DEBUG] Incident {i+1}:", file=sys.stderr)
                print(f"  Raw timestamp: {raw_timestamp}", file=sys.stderr)
                print(f"  With UTC->Local conversion: {timestamp_with_conversion}", file=sys.stderr)
                print(f"  No conversion (treat as local): {timestamp_no_conversion}", file=sys.stderr)
                print(f"  Corrected (+4h): {timestamp_corrected}", file=sys.stderr)
                print(f"  Component: {incident.get('componentName', 'N/A')}", file=sys.stderr)
        
        # Basic counts and metrics
        total_events = len(incidents)
        true_incidents = len([i for i in incidents if i.get("isIncident", False)])
        
        # Time range analysis
        if incidents:
            timestamps = [incident["timestamp"] for incident in incidents]
            first_incident = min(timestamps)
            last_incident = max(timestamps)
        else:
            first_incident = last_incident = None

        # Component and instance analysis (just unique counts)
        unique_components = len(set(incident.get("componentName", "Unknown") for incident in incidents))
        unique_instances = len(set(incident.get("instanceName", "Unknown") for incident in incidents))
        unique_patterns = len(set(incident.get("patternName", "Unknown") for incident in incidents))

        return {
            "status": "success",
            "system_name": system_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "summary": {
                "total_events": total_events,
                "true_incidents": true_incidents,
                "non_incident_events": total_events - true_incidents,
                "unique_components": unique_components,
                "unique_instances": unique_instances,
                "unique_patterns": unique_patterns,
                "first_event": format_api_timestamp_corrected(first_incident) if first_incident else None,
                "last_event": format_api_timestamp_corrected(last_incident) if last_incident else None,
                "has_incidents": true_incidents > 0
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_incidents_overview: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 1: Compact incident list (basic info only, no root cause details)
@mcp_server.tool()
async def get_incidents_list(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 10,
    only_true_incidents: bool = False
) -> Dict[str, Any]:
    """
    Fetches a compact list of incidents with basic information only.
    Use this after getting the overview to see individual incidents without overwhelming detail.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time_ms (int): Optional. The start of the time window in UTC milliseconds 
                          (typically midnight of the day to query).
        end_time_ms (int): Optional. The end of the time window in UTC milliseconds
                        (typically the current time or end of query window).
        limit (int): Maximum number of incidents to return (default: 10).
        only_true_incidents (bool): If True, only return events marked as true incidents.
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Filter for true incidents if requested
        if only_true_incidents:
            incidents = [i for i in incidents if i.get("isIncident", False)]
        
        # Sort by timestamp (most recent first) and limit
        incidents = sorted(incidents, key=lambda x: x["timestamp"], reverse=True)[:limit]

        # Create compact incident list
        incident_list = []
        for i, incident in enumerate(incidents):
            incident_info = {
                "id": i + 1,
                "timestamp": incident["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(incident["timestamp"]),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown")
            }
            incident_list.append(incident_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "total_found": len(result["data"]),
            "returned_count": len(incident_list),
            "incidents": incident_list
        }
        
    except Exception as e:
        error_message = f"Error in get_incidents_list: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 2: Detailed incident summary (includes root cause summary but still manageable)
@mcp_server.tool()
async def get_incidents_summary(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 5,
    only_true_incidents: bool = True
) -> Dict[str, Any]:
    """
    Fetches a detailed summary of incidents including root cause information.
    Use this when you need more detail about specific incidents after reviewing the compact list.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
        limit (int): Maximum number of incidents to return (default: 5).
        only_true_incidents (bool): If True, only return events marked as true incidents (default: True).
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Filter for true incidents if requested
        if only_true_incidents:
            incidents = [i for i in incidents if i.get("isIncident", False)]
        
        # Sort by timestamp (most recent first) and limit
        incidents = sorted(incidents, key=lambda x: x["timestamp"], reverse=True)[:limit]

        # Extract detailed summary information
        incidents_summary = []
        for incident in incidents:
            # Convert timestamp to human readable
            timestamp_str = format_api_timestamp_corrected(incident["timestamp"])
            
            summary = {
                "incident_id": len(incidents_summary) + 1,  # Simple ID for reference
                "timestamp": incident["timestamp"],
                "timestamp_human": timestamp_str,
                "instanceName": incident.get("instanceName", "Unknown"),
                "componentName": incident.get("componentName", "Unknown"),
                "patternName": incident.get("patternName", "Unknown"),
                "anomalyScore": incident.get("anomalyScore", 0),
                "status": incident.get("status", "unknown"),
                "isIncident": incident.get("isIncident", False),
                "has_raw_data": "rawData" in incident and incident["rawData"] is not None,
                "has_root_cause": "rootCause" in incident and incident["rootCause"] is not None,
            }
            
            # Add root cause summary if available
            if "rootCause" in incident and incident["rootCause"]:
                root_cause = incident["rootCause"]
                summary["root_cause_summary"] = {
                    "metricName": root_cause.get("metricName", "Unknown"),
                    "metricType": root_cause.get("metricType", "Unknown"),
                    "anomalyValue": root_cause.get("anomalyValue", 0),
                    "percentage": root_cause.get("percentage", 0),
                    "sign": root_cause.get("sign", "unknown"),
                    "isAlert": root_cause.get("isAlert", False)
                }
            
            incidents_summary.append(summary)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit
            },
            "time_range": {
                "start": start_time_ms,
                "end": end_time_ms,
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "total_found": len(result["data"]),
            "returned_count": len(incidents_summary),
            "incidents": incidents_summary
        }
        
    except Exception as e:
        error_message = f"Error in get_incidents_summary: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 3: Full incident details (without raw data)
@mcp_server.tool()
async def get_incident_details(
    system_name: str,
    incident_timestamp: int,
    include_root_cause: bool = True
) -> Dict[str, Any]:
    """
    Fetches complete information about a specific incident, excluding raw data to keep response manageable.
    Use this after identifying a specific incident from the list or summary layers.

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (int): The timestamp of the specific incident to get details for.
        include_root_cause (bool): Whether to include detailed root cause information.
    """
    try:
        # Get incidents for a small time window around the specific timestamp
        start_time = incident_timestamp - (5 * 60 * 1000)  # 5 minutes before
        end_time = incident_timestamp + (5 * 60 * 1000)    # 5 minutes after

        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time,
            end_time_ms=end_time,
        )

        if result["status"] != "success":
            return result

        # Find the specific incident
        target_incident = None
        for incident in result["data"]:
            if incident["timestamp"] == incident_timestamp:
                target_incident = incident
                break

        if not target_incident:
            return {"status": "error", "message": f"Incident with timestamp {incident_timestamp} not found"}

        # Build detailed response without raw data
        incident_details = {
            "timestamp": target_incident["timestamp"],
            "timestamp_human": format_api_timestamp_corrected(target_incident["timestamp"]),
            "instanceName": target_incident.get("instanceName"),
            "componentName": target_incident.get("componentName"),
            "patternName": target_incident.get("patternName"),
            "anomalyScore": target_incident.get("anomalyScore"),
            "status": target_incident.get("status"),
            "isIncident": target_incident.get("isIncident"),
            "active": target_incident.get("active"),
        }

        # Add root cause details if requested and available
        if include_root_cause and "rootCause" in target_incident and target_incident["rootCause"]:
            incident_details["rootCause"] = target_incident["rootCause"]

        # Add root cause info key if available
        if "rootCauseInfoKey" in target_incident:
            incident_details["rootCauseInfoKey"] = target_incident["rootCauseInfoKey"]

        # Add root cause result info if available
        if "rootCauseResultInfo" in target_incident:
            incident_details["rootCauseResultInfo"] = target_incident["rootCauseResultInfo"]

        return {
            "status": "success",
            "incident_details": incident_details,
            "has_raw_data": "rawData" in target_incident and target_incident["rawData"] is not None
        }
        
    except Exception as e:
        error_message = f"Error in get_incident_details: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 4: Raw data extraction (for deep investigation)
@mcp_server.tool()
async def get_incident_raw_data(
    system_name: str,
    incident_timestamp: int,
    max_length: int = 5000
) -> Dict[str, Any]:
    """
    Fetches the raw data (logs, stack traces) for a specific incident.
    Use this only when you need to examine the actual error logs or stack traces.

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (int): The timestamp of the specific incident.
        max_length (int): Maximum length of raw data to return (to prevent overwhelming the LLM).
    """
    try:
        # Get incidents for a small time window around the specific timestamp
        start_time = incident_timestamp - (5 * 60 * 1000)  # 5 minutes before
        end_time = incident_timestamp + (5 * 60 * 1000)    # 5 minutes after

        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time,
            end_time_ms=end_time,
        )

        if result["status"] != "success":
            return result

        # Find the specific incident
        target_incident = None
        for incident in result["data"]:
            if incident["timestamp"] == incident_timestamp:
                target_incident = incident
                break

        if not target_incident:
            return {"status": "error", "message": f"Incident with timestamp {incident_timestamp} not found"}

        raw_data = target_incident.get("rawData", "")
        if not raw_data:
            return {"status": "error", "message": "No raw data available for this incident"}

        # Truncate if too long
        if len(raw_data) > max_length:
            raw_data = raw_data[:max_length] + f"\n... [TRUNCATED - Full length: {len(target_incident['rawData'])} characters]"

        return {
            "status": "success",
            "incident_timestamp": incident_timestamp,
            "timestamp_human": format_api_timestamp_corrected(incident_timestamp),
            "instanceName": target_incident.get("instanceName"),
            "componentName": target_incident.get("componentName"),
            "raw_data": raw_data,
            "raw_data_length": len(target_incident.get("rawData", "")),
            "truncated": len(target_incident.get("rawData", "")) > max_length
        }
        
    except Exception as e:
        error_message = f"Error in get_incident_raw_data: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 5: Statistics and analysis tools
@mcp_server.tool()
async def get_incidents_statistics(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Provides statistical analysis of incidents for a system over a time period.
    Use this to understand incident patterns, frequency, and impact.

    Args:
        system_name (str): The name of the system to analyze.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Calculate statistics
        total_incidents = len(incidents)
        true_incidents = len([i for i in incidents if i.get("isIncident", False)])
        
        # Group by component
        components = {}
        instances = {}
        patterns = {}
        
        for incident in incidents:
            # Component analysis
            component = incident.get("componentName", "Unknown")
            components[component] = components.get(component, 0) + 1
            
            # Instance analysis
            instance = incident.get("instanceName", "Unknown")
            instances[instance] = instances.get(instance, 0) + 1
            
            # Pattern analysis
            pattern = incident.get("patternName", "Unknown")
            patterns[pattern] = patterns.get(pattern, 0) + 1

        return {
            "status": "success",
            "system_name": system_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "statistics": {
                "total_events": total_incidents,
                "true_incidents": true_incidents,
                "non_incident_events": total_incidents - true_incidents,
                "top_affected_components": dict(sorted(components.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_instances": dict(sorted(instances.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_patterns": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:10])
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_incidents_statistics: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Legacy/Simple tools for other event types (to be enhanced later)
@mcp_server.tool()
async def fetch_traces(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches trace timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for traces, distributed tracing, or application performance data.

    Args:
        system_name (str): The name of the system to query for traces.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with the timeline endpoint
        result = await api_client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        return result
        
    except Exception as e:
        error_message = f"Error in fetch_traces: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

@mcp_server.tool()
async def fetch_log_anomalies(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches log anomaly timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for log anomalies, unusual log patterns, or log-based issues.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with the timeline endpoint
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        return result
        
    except Exception as e:
        error_message = f"Error in fetch_log_anomalies: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

@mcp_server.tool()
async def fetch_deployments(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches deployment timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for deployments, releases, or change events.

    Args:
        system_name (str): The name of the system to query for deployments.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with the timeline endpoint
        result = await api_client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        return result
        
    except Exception as e:
        error_message = f"Error in fetch_deployments: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Add today-specific incident tool
@mcp_server.tool()
async def get_today_incidents(
    system_name: str,
    only_true_incidents: bool = True
) -> Dict[str, Any]:
    """
    Fetches incidents for today in the user's timezone.
    Use this tool when a user asks for "today's incidents", "incidents today", etc.

    Args:
        system_name (str): The name of the system to query for incidents.
        only_true_incidents (bool): If True, only return events marked as true incidents (default: True).
    """
    try:
        # Get today's time range in user's timezone
        start_time_ms, end_time_ms = get_today_time_range_ms()

        # Call the InsightFinder API client
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Filter for true incidents if requested
        if only_true_incidents:
            incidents = [i for i in incidents if i.get("isIncident", False)]
        
        # Sort by timestamp (most recent first)
        incidents = sorted(incidents, key=lambda x: x["timestamp"], reverse=True)

        # Create incident list with timezone-aware timestamps
        incident_list = []
        for i, incident in enumerate(incidents):
            incident_info = {
                "id": i + 1,
                "timestamp": incident["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(incident["timestamp"]),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown")
            }
            incident_list.append(incident_info)

        return {
            "status": "success",
            "system_name": system_name,
            "query_type": "today_incidents",
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms),
                "description": "Today in user's timezone"
            },
            "total_found": len(result["data"]),
            "true_incidents_found": len(incident_list),
            "incidents": incident_list
        }
        
    except Exception as e:
        error_message = f"Error in get_today_incidents: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}