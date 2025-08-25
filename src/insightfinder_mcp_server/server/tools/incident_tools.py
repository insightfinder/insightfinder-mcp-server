import sys
import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
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
    project_name: Optional[str] = None
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
        project_name (str): Optional. Filter results to only include incidents from this specific project.

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
        api_client = _get_api_client()
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
        
        # Filter by project name if specified
        if project_name:
            # incidents = [i for i in incidents if i.get("projectName") == project_name]
            incidents = [i for i in incidents if i.get("projectName", "").lower() == project_name.lower() or i.get("projectDisplayName", "").lower() == project_name.lower()]
        
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
        unique_projects = len(set(incident.get("projectDisplayName", "Unknown") for incident in incidents))

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
                "unique_projects": unique_projects,
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
    only_true_incidents: bool = True
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
        only_true_incidents (bool): If True, only return events marked as true incidents. default is True.
    
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
        api_client = _get_api_client()
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
                "project": incident.get("projectDisplayName", "Unknown"),
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
    only_true_incidents: bool = True,
    include_root_cause_info: bool = True
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
        include_root_cause_info (bool): If True, include information about root cause availability (default: True).
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
        api_client = _get_api_client()
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
                "projectName": incident.get("projectDisplayName", "Unknown"),
                "instanceName": incident.get("instanceName", "Unknown"),
                "componentName": incident.get("componentName", "Unknown"),
                "patternName": incident.get("patternName", "Unknown"),
                "anomalyScore": incident.get("anomalyScore", 0),
                "status": incident.get("status", "unknown"),
                "isIncident": incident.get("isIncident", False),
                "has_raw_data": "rawData" in incident and incident["rawData"] is not None,
                "has_root_cause": incident.get('rootCauseResultInfo', {}).get('hasPrecedingEvent', False) or ("rootCause" in incident and incident["rootCause"] is not None),
            }
            
            # Add root cause information if available
            if include_root_cause_info:
                root_cause_info = {}
                
                # Basic root cause info from the incident
                # if "rootCause" in incident and incident["rootCause"]:
                #     root_cause = incident["rootCause"]
                #     root_cause_info["summary"] = {
                #         "metricName": root_cause.get("metricName", "Unknown"),
                #         "metricType": root_cause.get("metricType", "Unknown"),
                #         "anomalyValue": root_cause.get("anomalyValue", 0),
                #         "percentage": root_cause.get("percentage", 0),
                #         "sign": root_cause.get("sign", "unknown"),
                #         "isAlert": root_cause.get("isAlert", False)
                #     }
                
                # Add root cause result info if available
                if "rootCauseResultInfo" in incident and incident["rootCauseResultInfo"]:
                    root_cause_info["result_info"] = {
                        "hasPrecedingEvent": incident["rootCauseResultInfo"].get("hasPrecedingEvent", False),
                        "hasTrailingEvent": incident["rootCauseResultInfo"].get("hasTrailingEvent", False),
                        "causedByChangeEvent": incident["rootCauseResultInfo"].get("causedByChangeEvent", False),
                        "leadToIncident": incident["rootCauseResultInfo"].get("leadToIncident", False)
                    }
                
                # Add root cause info key if available (used for fetching full RCA chain)
                if "rootCauseInfoKey" in incident and incident["rootCauseInfoKey"]:
                    root_cause_info["info_key"] = {
                        "projectName": incident["rootCauseInfoKey"].get("projectName"),
                        "instanceName": incident["rootCauseInfoKey"].get("instanceName"),
                        "incidentTimestamp": incident["rootCauseInfoKey"].get("incidentTimestamp")
                    }
                
                if root_cause_info:
                    summary["root_cause_info"] = root_cause_info
            
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
    include_root_cause: bool = True,
    fetch_rca_chain: bool = False,
    include_recommendations: bool = False
) -> Dict[str, Any]:
    """
    Fetches complete information about a specific incident, excluding raw data to keep response manageable.
    Use this after identifying a specific incident from the list or summary layers.

    Root Cause/Causal Chain Policy:
    - Please always fetch and include the full root cause analysis (RCA) chain in the response.
    - Always display the full RCA chain at once, including all available timestamps, project names, and details (with cdn if available) for each event in the chain.
    - The response should include the entire causal chain, not just a summary or a single root cause event.
    - This ensures users get a complete, timestamped view of the incident's causal sequence.

    Recommendations:
    - Please always fetch and include the full root cause analysis (RCA) chain in the response.
    - When a user requests recommendations or remediation steps, set `include_recommendations=True`.
    - If recommendations are available for the incident (via LLM or other sources), they will be included in the response under the `recommendation` field.
    - The response will indicate whether recommendations are available with the `recommendation_available` flag.
    - Recommendations may include suggested actions, remediation steps, or insights generated by the system.
    - If no recommendations are available, the response will indicate this.

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (int): The timestamp of the specific incident to get details for.
        include_root_cause (bool): Whether to include detailed root cause information.
        fetch_rca_chain (bool): Whether to fetch the full root cause analysis chain (always set to True when user requests root cause or causal chain).
        include_recommendations (bool): Whether to include recommendations or remediation steps if available.
    """
    try:
        # Use a 5-minute window around the incident timestamp
        window_ms = 5 * 60 * 1000  # 5 minutes in milliseconds
        start_time = incident_timestamp - window_ms
        end_time = incident_timestamp + window_ms
        
        client = _get_api_client()
        incidents_response = await client._fetch_timeline_data(
            "incident",
            system_name,
            start_time,
            end_time
        )

        # Find the specific incident in the response
        incidents = incidents_response.get('data', [])
        incident_data = None
        for inc in incidents:
            if inc.get('timestamp') == incident_timestamp:
                incident_data = inc
                break
                
        if not incident_data:
            return {"status": "error", "message": "No incident found with the specified timestamp"}
        result = {
            "incident": incident_data,
            "raw_data_available": True,  # Indicate that raw data can be fetched separately
            "root_cause_available": False,
            "root_cause_chain": None,
            "recommendation_available": False,
            "recommendation": None,
            "anomalyScore": incident_data.get("anomalyScore"),
            "status": incident_data.get("status"),
            "isIncident": incident_data.get("isIncident"),
            "active": incident_data.get("active")
        }

        # Check if root cause analysis is available and requested
        root_cause_info = incident_data.get('rootCauseInfoKey')
        if include_root_cause and root_cause_info and fetch_rca_chain:
            try:
                # print(f"[DEBUG] Fetching RCA chain for rootCauseInfoKey: {root_cause_info}", file=sys.stderr)
                rca_data = await client.fetch_root_cause_analysis(
                    root_cause_info_key=root_cause_info,
                    customer_name=incident_data.get('userName', '')
                )
                rca_chain = rca_data.get('rcaChainList', [])
                # Sort the RCA chain by the earliest eventTimestamp in each rcaNodeList
                def get_min_event_timestamp(chain_item):
                    node_list = chain_item.get('rcaNodeList', [])
                    timestamps = [node.get('eventTimestamp', float('inf')) for node in node_list if 'eventTimestamp' in node]
                    return min(timestamps) if timestamps else float('inf')
                if isinstance(rca_chain, list) and rca_chain and 'rcaNodeList' in rca_chain[0]:
                    rca_chain = sorted(rca_chain, key=get_min_event_timestamp)

                # Optionally, sort each rcaNodeList by eventTimestamp as well
                for chain_item in rca_chain:
                    node_list = chain_item.get('rcaNodeList', [])
                    if isinstance(node_list, list) and node_list and 'eventTimestamp' in node_list[0]:
                        # Replace sourceProjectName with sourceProjectDisplayName in each node
                        for node in node_list:
                            if 'sourceProjectDisplayName' in node:
                                node['sourceProjectName'] = node['sourceProjectDisplayName']
                                # Try to update projectName in sourceDetail JSON if possible
                                if 'sourceDetail' in node and node['sourceDetail']:
                                    import json
                                    try:
                                        detail_obj = json.loads(node['sourceDetail']) # ////////////
                                        # if 'projectName' in detail_obj:
                                        #     detail_obj['projectName'] = node['sourceProjectDisplayName']
                                        #     node['sourceDetail'] = json.dumps(detail_obj)

                                        # Add parsed fields for easy access
                                        # print(f"[DEBUG] Parsing sourceDetail JSON: {detail_obj}", file=sys.stderr)
                                        if detail_obj and detail_obj.get('content'):
                                            content_str = detail_obj['content']
                                            import json as _json
                                            try:
                                                content = _json.loads(content_str) if isinstance(content_str, str) else content_str
                                            except Exception:
                                                content = content_str
                                            # Extract common fields if they exist
                                            common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time", "execution_uid"]
                                            extracted_fields = {}
                                            for field in common_fields:
                                                if isinstance(content, dict) and field in content:
                                                    extracted_fields[field] = content[field]
                                        
                                            if extracted_fields:
                                                node["key_fields"] = extracted_fields
                                        
                                        node.pop('sourceDetail', None)  # Remove the original sourceDetail to reduce clutter
                                        # print(f"[DEBUG] Updated node after parsing sourceDetail: {node}", file=sys.stderr)
                                    except Exception:
                                        pass
                        chain_item['rcaNodeList'] = sorted(node_list, key=lambda n: n.get('eventTimestamp', 0))

                result['root_cause_available'] = True
                # include the count of events in the chain
                result['root_cause_chain_event_count'] = sum(len(item.get('rcaNodeList', [])) for item in rca_chain)
                result['root_cause_chain'] = rca_chain
                # print(f"[DEBUG] RCA chain fetch result: {str(result['root_cause_chain'])}", file=sys.stderr)
                # print(f"[DEBUG] RCA chain event count: {result['root_cause_chain_event_count']}", file=sys.stderr)
            except Exception as e:
                logger.warning(f"Failed to fetch root cause analysis: {str(e)}")
        
        # Check if root cause info is available in the incident data
        if include_root_cause and incident_data.get('rootCauseResultInfo', {}).get('hasPrecedingEvent', False):
            result['root_cause_available'] = True
            if not result.get('root_cause_chain'):
                result['root_cause_chain'] = []
        
        # Fetch recommendations if requested and incident LLM key is available
        if include_recommendations and 'incidentLLMKey' in incident_data and incident_data['incidentLLMKey']:
            # print(f"[DEBUG] Fetching recommendations for incidentLLMKey: {incident_data['incidentLLMKey']}")
            try:
                recommendation = await client.fetch_recommendation(
                    incident_llm_key=incident_data['incidentLLMKey'],
                    customer_name=incident_data.get('userName', '')
                )
                if recommendation:
                    result['recommendation_available'] = True
                    result['recommendation'] = recommendation
                    # print(f"[DEBUG] Recommendation fetch result: {str(result['recommendation'])}", file=sys.stderr)
            except Exception as e:
                logger.warning(f"Failed to fetch recommendations: {str(e)}")

        return result

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

        api_client = _get_api_client()
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
            "projectName": target_incident.get("projectDisplayName"),
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

        api_client = _get_api_client()
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
        projects = {}
        
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
            
            # Project analysis
            project = incident.get("projectDisplayName", "Unknown")
            projects[project] = projects.get(project, 0) + 1

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
                "top_patterns": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_projects": dict(sorted(projects.items(), key=lambda x: x[1], reverse=True)[:10])
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
        api_client = _get_api_client()
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
        api_client = _get_api_client()
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
        api_client = _get_api_client()
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

# Project-specific incident query tool
@mcp_server.tool()
async def get_project_incidents(
    system_name: str,
    project_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    only_true_incidents: bool = True,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Fetches incidents specifically for a given project within a system.
    Use this tool when the user specifies both a system name and project name.
    
    Example usage:
    - "show me incidents for project demo-kpi-metrics-2 in system Citizen Cane Demo System (STG)"
    - "get incidents after timestamp for project X in system Y"

    Args:
        system_name (str): The name of the system (e.g., "Citizen Cane Demo System (STG)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time_ms (int): Start time in UTC milliseconds
        end_time_ms (int): End time in UTC milliseconds  
        only_true_incidents (bool): If True, only return events marked as true incidents
        limit (int): Maximum number of incidents to return (default: 20)
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with ONLY the system name
        api_client = _get_api_client()
        result = await api_client.get_incidents(
            system_name=system_name,  # Use only the system name here
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Filter by the specific project name
        # project_incidents = [i for i in incidents if i.get("projectName") == project_name]
        project_incidents = [i for i in incidents if i.get("projectName", "").lower() == project_name.lower() or i.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Filter for true incidents if requested
        if only_true_incidents:
            project_incidents = [i for i in project_incidents if i.get("isIncident", False)]
        
        # Sort by timestamp (most recent first) and limit
        project_incidents = sorted(project_incidents, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]

        # Create detailed incident list for the project
        incident_list = []
        for i, incident in enumerate(project_incidents):
            incident_info = {
                "id": i + 1,
                "timestamp": incident["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(incident["timestamp"]),
                "project": incident.get("projectDisplayName", "Unknown"),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown"),
                "active": incident.get("active", False)
            }
            
            # Add root cause summary if available
            if "rootCause" in incident and incident["rootCause"]:
                root_cause = incident["rootCause"]
                incident_info["root_cause"] = {
                    "metricName": root_cause.get("metricName", "Unknown"),
                    "metricType": root_cause.get("metricType", "Unknown"),
                    "anomalyValue": root_cause.get("anomalyValue", 0),
                    "percentage": root_cause.get("percentage", 0),
                    "sign": root_cause.get("sign", "unknown")
                }
            
            incident_list.append(incident_info)

        return {
            "status": "success",
            "query_type": "project_specific_incidents",
            "system_name": system_name,
            "project_name": project_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit
            },
            "total_system_incidents": len(incidents),
            "project_incidents_found": len([i for i in incidents if i.get("projectDisplayName") == project_name or i.get("projectName") == project_name]),
            "returned_count": len(incident_list),
            "incidents": incident_list
        }
        
    except Exception as e:
        error_message = f"Error in get_project_incidents: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

@mcp_server.tool()
async def predict_incidents(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
) -> Dict[str, Any]:
    """
    Predicts future incidents for a system in a given time window.
    Uses the InsightFinder prediction API to fetch predicted incidents.
    This will include recommendations for each predicted incident if available.
    
    Note:
        The timestamp for each predicted incident is always taken from the top-level 'timestamp_prediction' field of the incident object.

    Args:
        system_name (str): The name of the system to predict incidents for.
        start_time_ms (int): Start of the prediction window (UTC ms).
        end_time_ms (int): End of the prediction window (UTC ms).

    Returns:
        Dict[str, Any]: Prediction results, including recommendations if any are available.
    """
    try:
        # Security checks
        if not system_name or len(system_name) > 100:
            return {"status": "error", "message": "Invalid system_name"}

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.predict_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        timeline_list = result["data"]

        timeline_list = sorted(timeline_list, key=lambda x: x.get("predictionTime", 0), reverse=False)

        # # Remove rootCause/rootCauseResultInfo/rootCauseInfoKey and handle projectName
        # for incident in timeline_list:
        #     incident.pop("rootCause", None)
        #     incident.pop("rootCauseResultInfo", None)
        #     incident.pop("rootCauseInfoKey", None)
        #     incident.pop("projectName", None)
        #     # Rename projectDisplayName to projectName
        #     if "projectDisplayName" in incident:
        #         incident["projectName"] = incident.pop("projectDisplayName")
        #     # Handle raw_data field
        #     if not include_raw_data:
        #         incident.pop("rawData", None)
            
        #     print(f"[DEBUG] Predicted incident timestamp: {incident.get('timestamp')} - {format_api_timestamp_corrected(incident.get('timestamp'))}", file=sys.stderr)


        incident_list = []
        for i, incident in enumerate(timeline_list):
            incident_info = {
                "id": i + 1,
                "timestamp_prediction": incident["predictionTime"],
                "timestamp_prediction_human": format_api_timestamp_corrected(incident["predictionTime"]),
                "timestamp_occurence_prediction": incident["predictionOccurenceTime"],
                "timestamp_occurence_prediction_human": format_api_timestamp_corrected(incident["predictionOccurenceTime"]),
                "project": incident.get("projectDisplayName", "Unknown"),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown"),
                "active": incident.get("active", False)
            }

            incident_llm_key = incident.get("incidentLLMKey")
            user_name = incident.get("userName", "")
            if incident_llm_key:
                try:
                    recommendation = await api_client.fetch_recommendation(
                        incident_llm_key=incident_llm_key,
                        customer_name=user_name
                    )
                    if recommendation:
                        incident_info["recommendation"] = recommendation
                except Exception as e:
                    pass # Ignore recommendation fetch errors

            incident_list.append(incident_info)

        # Optionally fetch recommendations for each predicted incident
        # include_recommendations = True  # Always true for predicted incidents
        # if include_recommendations:
        #     for incident in timeline_list:
        #         incident_llm_key = incident.get("incidentLLMKey")
        #         user_name = incident.get("userName", "")
        #         if incident_llm_key:
        #             try:
        #                 recommendation = await api_client.fetch_recommendation(
        #                     incident_llm_key=incident_llm_key,
        #                     customer_name=user_name
        #                 )
        #                 if recommendation:
        #                     incident["recommendation"] = recommendation
        #             except Exception as e:
        #                 pass # Ignore recommendation fetch errors

        return {
            "status": "success",
            "system_name": system_name,
            "time_range": {
                "start": start_time_ms,
                "end": end_time_ms,
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "predicted_incidents": incident_list,
            "returned_count": len(incident_list)
        }
    except Exception as e:
        error_message = f"Error in predict_incidents: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message)
        return {"status": "error", "message": error_message}

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