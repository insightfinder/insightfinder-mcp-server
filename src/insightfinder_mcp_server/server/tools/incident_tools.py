import sys
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from ..server import mcp_server
from ...api_client.insightfinder_client import api_client
from ...config.settings import settings
from .get_time import get_timezone_aware_timestamp_ms, get_timezone_aware_time_range_ms, format_timestamp_in_user_timezone, get_today_time_range_ms, format_timestamp_no_conversion, format_api_timestamp_corrected

"""
=== INCIDENT INVESTIGATION TOOLS - LLM USAGE GUIDELINES ===

IMPORTANT: All timestamp parameters in these tools use UTC milliseconds format.

🕐 TIMESTAMP FORMAT REQUIREMENTS:
- start_time_ms: UTC timestamp in milliseconds (e.g., 1691234567890)
- end_time_ms: UTC timestamp in milliseconds (e.g., 1691234567890)
- Default behavior: If not provided, tools automatically use last 24 hours

📅 TIME RANGE BEST PRACTICES:
1. For "today's incidents": Use get_today_incidents() tool
2. For specific dates: Convert user's local date to UTC midnight-to-midnight
3. For time periods: Convert user's timezone to UTC range
4. For specific times: Always convert local time to UTC milliseconds

🔧 WHEN TO USE EACH TOOL (Progressive Investigation):

Layer 0 - OVERVIEW (Start here):
├── get_incidents_overview() - Quick counts and basic metrics
├── get_today_incidents() - Today's incidents in user timezone
├── get_utc_time_range_for_query() - Convert time descriptions to UTC
└── Use when: User asks "any incidents?" or "what happened today?"

Layer 1 - LIST (Browse incidents):
├── get_incidents_list() - Compact list with basic info
└── Use when: Need to see individual incidents without detail

Layer 2 - SUMMARY (Get context):
├── get_incidents_summary() - Detailed summary with root cause
└── Use when: Need understanding of specific incidents

Layer 3 - DETAILS (Investigate specific):
├── get_incident_details() - Full incident information
└── Use when: User wants complete info about one incident

Layer 4 - RAW DATA (Deep dive):
├── get_incident_raw_data() - Stack traces, logs, error details
└── Use when: Need actual error messages or stack traces

Layer 5 - ANALYTICS (Pattern analysis):
├── get_incidents_statistics() - Patterns, trends, frequency
└── Use when: Need to understand patterns or root causes

🎯 QUERY PARAMETER GUIDELINES:

Time Ranges:
- start_time_ms: UTC midnight of start date (e.g., for Aug 7, 2025: start of day UTC)
- end_time_ms: UTC end of period or current time
- If omitted: Tools default to last 24 hours automatically

System Names:
- Always required: system_name parameter
- Use exact system identifier from user's environment

Filtering:
- only_true_incidents=True: For confirmed incidents only
- limit: Control response size (default varies by tool)
- include_root_cause=True: Include diagnostic information

🚨 COMMON USER QUESTIONS → TOOL MAPPING:

"Any incidents today?" → get_today_incidents()
"What happened yesterday?" → get_utc_time_range_for_query("yesterday") then get_incidents_overview()
"List recent incidents" → get_incidents_list()
"Tell me about incident X" → get_incident_details() with specific timestamp
"Why did the system fail?" → get_incidents_summary() then get_incident_raw_data()
"Show me patterns" → get_incidents_statistics()
"What broke the most?" → get_incidents_statistics() for top components

💡 TIMEZONE HANDLING:
- All API queries use UTC internally
- Tools automatically handle timezone conversion for display
- User sees human-readable times in their timezone
- When user specifies times, convert to UTC milliseconds
- Use get_utc_time_range_for_query() to convert time descriptions

⚡ PERFORMANCE TIPS:
- Start with overview tools for quick assessment
- Use specific timestamp when drilling down
- Limit raw data requests to avoid overwhelming responses
- Filter for true incidents when investigating real issues

🔍 INVESTIGATION WORKFLOW EXAMPLE:
1. get_incidents_overview() - "Are there any incidents?"
2. get_incidents_list() - "Show me what happened"
3. get_incidents_summary() - "Tell me more about these incidents"
4. get_incident_details() - "Investigate this specific incident"
5. get_incident_raw_data() - "Show me the actual error"
6. get_incidents_statistics() - "What patterns do we see?"

📋 COMPLETE USAGE EXAMPLE:

User: "Show me incidents from yesterday 9 AM to 5 PM"

Step 1: Convert time range
→ get_utc_time_range_for_query("yesterday 9 AM to 5 PM")

Step 2: Get overview
→ get_incidents_overview(system_name="prod-web", start_time_ms=1723114800000, end_time_ms=1723143600000)

Step 3: Get details if incidents found
→ get_incidents_list(system_name="prod-web", start_time_ms=1723114800000, end_time_ms=1723143600000)

Step 4: Investigate specific incident
→ get_incident_details(system_name="prod-web", incident_timestamp=1723125400000)

Remember: Always start with overview tools and progressively drill down!
"""

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
        start_time_ms (int): Optional. The start of the time window in UTC milliseconds.
                         If not provided, defaults to 24 hours ago.
                         Example: For Aug 7, 2025 00:00 UTC = 1723075200000
        end_time_ms (int): Optional. The end of the time window in UTC milliseconds.
                       If not provided, defaults to the current time.
                       Example: For Aug 7, 2025 23:59 UTC = 1723161540000

    Time Conversion Examples:
        - "Today's incidents" → use get_today_incidents() instead
        - "Yesterday 9 AM to 5 PM EST" → convert to UTC: (9 AM EST = 1 PM UTC, 5 PM EST = 9 PM UTC)
        - "Last 24 hours" → omit parameters (uses default range)
        - "This week" → start_time_ms=Monday_midnight_UTC, end_time_ms=current_time_UTC
    """
    # Simple security checks
    if not system_name or len(system_name) > 100:
        return {"status": "error", "message": "Invalid system_name"}
    
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
        start_time_ms (int): Optional. The start of the time window in UTC milliseconds.
                          Example: For midnight Aug 7, 2025 UTC = 1723075200000
        end_time_ms (int): Optional. The end of the time window in UTC milliseconds.
                        Example: For end of Aug 7, 2025 UTC = 1723161599000
        limit (int): Maximum number of incidents to return (default: 10).
        only_true_incidents (bool): If True, only return events marked as true incidents.
    
    UTC Conversion Notes:
        - Always provide timestamps in UTC milliseconds format
        - Use tools like get_current_datetime() or get_time_range_query() for conversion
        - Default range is last 24 hours if parameters omitted
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
    # Security checks
    if not system_name or len(system_name) > 100:
        return {"status": "error", "message": "Invalid system_name"}
    
    # Limit max_length to prevent abuse
    max_length = min(max_length, 10000)
    
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

# Helper tool for LLMs to convert time ranges to proper UTC milliseconds
@mcp_server.tool()
async def get_utc_time_range_for_query(
    starttime: str,
    endtime: Optional[str] = None
) -> Dict[str, Any]:
    """
    Converts specific start time and optional end time to UTC milliseconds for incident queries.
    Accepts only these formats: "MM-DD-YYYY", "MM-DD-YYYY HH:MM", "MM-DD".
    
    Args:
        starttime (str): Start time in format "MM-DD-YYYY", "MM-DD-YYYY HH:MM", or "MM-DD"
        endtime (str, optional): End time in same formats. If not provided, defaults to current time.
    
    Supported formats:
        - "MM-DD-YYYY" (e.g., "08-07-2025") - defaults to 00:00 for start, 23:59 for end
        - "MM-DD-YYYY HH:MM" (e.g., "08-07-2025 14:30")
        - "MM-DD" (e.g., "08-07") - assumes current year
    """
    try:
        import re
        
        # Get current time in UTC for defaults
        now_utc = datetime.now(timezone.utc)
        current_year = now_utc.year
        current_ms = int(now_utc.timestamp() * 1000)
        
        def parse_time_string(time_str: str, is_end_time: bool = False) -> int:
            """Parse time string into UTC milliseconds"""
            time_str = time_str.strip()
            
            # Pattern 1: MM-DD-YYYY HH:MM
            if re.match(r'^\d{1,2}-\d{1,2}-\d{4}\s+\d{1,2}:\d{2}$', time_str):
                dt = datetime.strptime(time_str, "%m-%d-%Y %H:%M")
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            
            # Pattern 2: MM-DD-YYYY
            elif re.match(r'^\d{1,2}-\d{1,2}-\d{4}$', time_str):
                dt = datetime.strptime(time_str, "%m-%d-%Y")
                if is_end_time:
                    # For end time, set to 23:59:59
                    dt = dt.replace(hour=23, minute=59, second=59)
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            
            # Pattern 3: MM-DD (assume current year)
            elif re.match(r'^\d{1,2}-\d{1,2}$', time_str):
                time_str_with_year = f"{time_str}-{current_year}"
                dt = datetime.strptime(time_str_with_year, "%m-%d-%Y")
                if is_end_time:
                    # For end time, set to 23:59:59
                    dt = dt.replace(hour=23, minute=59, second=59)
                return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            
            else:
                raise ValueError(f"Invalid time format: '{time_str}'")
        
        # Parse start time
        try:
            start_ms = parse_time_string(starttime, is_end_time=False)
        except ValueError as e:
            return {
                "status": "error",
                "message": f"Invalid starttime format: '{starttime}'",
                "supported_formats": ["MM-DD-YYYY", "MM-DD-YYYY HH:MM", "MM-DD"],
                "examples": ["08-07-2025", "08-07-2025 14:30", "08-07"],
                "error": str(e)
            }
        
        # Parse end time or use current time
        if endtime is not None:
            try:
                end_ms = parse_time_string(endtime, is_end_time=True)
            except ValueError as e:
                return {
                    "status": "error",
                    "message": f"Invalid endtime format: '{endtime}'",
                    "supported_formats": ["MM-DD-YYYY", "MM-DD-YYYY HH:MM", "MM-DD"],
                    "examples": ["08-07-2025", "08-07-2025 17:00", "08-07"],
                    "error": str(e)
                }
        else:
            end_ms = current_ms
            endtime = "current time"
        
        # Validate time range
        if end_ms <= start_ms:
            return {
                "status": "error",
                "message": "End time must be after start time",
                "starttime": starttime,
                "endtime": endtime
            }
        
        # Calculate duration
        duration_hours = (end_ms - start_ms) / (1000 * 60 * 60)
        
        return {
            "status": "success",
            "input": {
                "starttime": starttime,
                "endtime": endtime
            },
            "query_parameters": {
                "start_time_ms": start_ms,
                "end_time_ms": end_ms
            },
            "human_readable": {
                "start_time": format_timestamp_in_user_timezone(start_ms),
                "end_time": format_timestamp_in_user_timezone(end_ms),
                "duration_hours": round(duration_hours, 1),
                "description": f"Time range: {starttime} to {endtime}"
            },
            "usage_example": f"get_incidents_overview(system_name='your-system', start_time_ms={start_ms}, end_time_ms={end_ms})"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error parsing time range: {str(e)}",
            "input": {"starttime": starttime, "endtime": endtime},
            "help": "Use formats: MM-DD-YYYY, MM-DD-YYYY HH:MM, or MM-DD"
        }

# Helper tool for LLMs to convert specific start/end times to UTC milliseconds
@mcp_server.tool()
async def parse_specific_time_range(
    start_time: str,
    end_time: str,
    timezone_hint: str = "user's local timezone"
) -> Dict[str, Any]:
    """
    Converts specific start_time and end_time strings to UTC milliseconds for incident queries.
    Use this tool when you have explicit start and end times in MM-DD-YYYY HH:MM format.
    
    Args:
        start_time (str): Start time in format MM-DD-YYYY HH:MM (e.g., "08-07-2025 09:00")
        end_time (str): End time in format MM-DD-YYYY HH:MM (e.g., "08-07-2025 17:00") 
        timezone_hint (str): Optional timezone context (for documentation purposes)
    
    Returns:
        Dictionary with start_time_ms and end_time_ms in UTC milliseconds, plus validation info.
    
    Examples:
        - start_time="08-07-2025 09:00", end_time="08-07-2025 17:00" → Work day on Aug 7
        - start_time="08-06-2025 00:00", end_time="08-06-2025 23:59" → Full day Aug 6
    """
    try:
        # Parse start time
        try:
            start_dt = datetime.strptime(start_time, "%m-%d-%Y %H:%M")
            # Assume user's local timezone, but treat as UTC for API purposes
            start_ms = int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid start_time format: '{start_time}'",
                "expected_format": "MM-DD-YYYY HH:MM",
                "example": "08-07-2025 09:00"
            }
        
        # Parse end time
        try:
            end_dt = datetime.strptime(end_time, "%m-%d-%Y %H:%M")
            # Assume user's local timezone, but treat as UTC for API purposes
            end_ms = int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid end_time format: '{end_time}'",
                "expected_format": "MM-DD-YYYY HH:MM",
                "example": "08-07-2025 17:00"
            }
        
        # Validate time range
        if end_ms <= start_ms:
            return {
                "status": "error",
                "message": "End time must be after start time",
                "start_time": start_time,
                "end_time": end_time
            }
        
        # Calculate duration
        duration_hours = (end_ms - start_ms) / (1000 * 60 * 60)
        
        return {
            "status": "success",
            "input": {
                "start_time": start_time,
                "end_time": end_time,
                "timezone_hint": timezone_hint
            },
            "query_parameters": {
                "start_time_ms": start_ms,
                "end_time_ms": end_ms
            },
            "human_readable": {
                "start_time": format_timestamp_in_user_timezone(start_ms),
                "end_time": format_timestamp_in_user_timezone(end_ms),
                "duration_hours": round(duration_hours, 1),
                "description": f"Custom time range: {start_time} to {end_time}"
            },
            "usage_example": f"get_incidents_overview(system_name='your-system', start_time_ms={start_ms}, end_time_ms={end_ms})"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error parsing time range: {str(e)}",
            "input": {"start_time": start_time, "end_time": end_time},
            "help": "Use format MM-DD-YYYY HH:MM for both start_time and end_time"
        }