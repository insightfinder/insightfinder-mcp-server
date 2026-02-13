import sys
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    format_timestamp_in_user_timezone,
    format_api_timestamp_corrected,
    parse_user_datetime_to_ms,
    convert_to_ms,
)


# Layer 0: Ultra-compact incident overview (just counts and basic info)
@mcp_server.tool()
async def get_incidents_overview(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of incidents - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration.
    Use this tool when a user first asks about incidents to get a quick overview.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time (str): Optional. The start of the time window.
                         Accepts human-readable formats: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                         If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                       Accepts human-readable formats: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                       If not provided, defaults to the current time.
        project_name (str): Optional. Filter results to only include incidents from this specific project.

    Time Conversion Examples:
        - "Today's incidents" → omit time parameters (uses default last 24 hours)
        - "Yesterday 9 AM to 5 PM" → start_time="2026-02-11T09:00:00", end_time="2026-02-11T17:00:00"
        - "Last 24 hours" → omit parameters (uses default range)
        - "This week" → start_time="2026-02-09" (Monday's date)

    Note: All timestamps are in the Owner User Timezone. Display times using the
    "timezone" field from the response, never label as UTC.
    """
    # Simple security checks
    if not system_name or len(system_name) > 100:
        return {"status": "error", "message": "Invalid system_name"}
    
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
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
        # print(f"[DEBUG] Using time range: {start_time_ms} to {end_time_ms}", file=sys.stderr)

        
        # If start and end time are the same (e.g. user provided "2026-02-12" for both),
        # expand to cover the full day (00:00:00.000 to 23:59:59.999).
        # We treat the timestamp as UTC because it's already "fake UTC" (owner wall-clock).
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            import datetime
            dt = datetime.datetime.fromtimestamp(start_time_ms / 1000, tz=datetime.timezone.utc)
            # Set to start of day
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            # Set to end of day
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)
            
            if settings.ENABLE_DEBUG_MESSAGES:
                logger.debug(f"Expanded equal start/end time to full day: {start_time_ms} - {end_time_ms}")

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if settings.ENABLE_DEBUG_MESSAGES:
            logger.debug("Overview query - tz=%s, range=%s to %s", tz_name, start_time_ms, end_time_ms)

        if result["status"] != "success":
            return result

        incidents = result["data"]
        
        # Filter by project name if specified
        if project_name:
            incidents = [i for i in incidents if i.get("projectName", "").lower() == project_name.lower() or i.get("projectDisplayName", "").lower() == project_name.lower()]
        
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
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "summary": {
                "total_events": total_events,
                "true_incidents": true_incidents,
                "non_incident_events": total_events - true_incidents,
                "unique_components": unique_components,
                "unique_instances": unique_instances,
                "unique_patterns": unique_patterns,
                "unique_projects": unique_projects,
                "first_event": format_api_timestamp_corrected(first_incident, tz_name) if first_incident else None,
                "last_event": format_api_timestamp_corrected(last_incident, tz_name) if last_incident else None,
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 10,
    only_true_incidents: bool = True
) -> Dict[str, Any]:
    """
    Fetches a compact list of incidents with basic information only.
    Use this after getting the overview to see individual incidents without overwhelming detail.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time (str): Optional. The start of the time window.
                          Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                          If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                        Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                        If not provided, defaults to the current time.
        limit (int): Maximum number of incidents to return (default: 10).
        only_true_incidents (bool): If True, only return events marked as true incidents. default is True.
    
    Note: All timestamps are in the Owner User Timezone. Display times using the
    "timezone" field from the response, never label as UTC.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
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



        # If start and end time are the same (e.g. user provided "2026-02-12" for both),
        # expand to cover the full day (00:00:00.000 to 23:59:59.999).
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            import datetime
            dt = datetime.datetime.fromtimestamp(start_time_ms / 1000, tz=datetime.timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)
            
            if settings.ENABLE_DEBUG_MESSAGES:
                logger.debug(f"Expanded equal start/end time to full day: {start_time_ms} - {end_time_ms}")

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
                "timestamp_human": format_api_timestamp_corrected(incident["timestamp"], tz_name),
                "projectDisplayName": incident.get("projectDisplayName", "Unknown"),
                "realProjectName": incident.get("projectName", "Unknown"),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
            }
            
            # Add metric name right after instance only if available
            if "rootCause" in incident and incident["rootCause"] and "metricName" in incident["rootCause"]:
                incident_info["metricName"] = incident["rootCause"]["metricName"]
            
            # Add remaining fields
            incident_info.update({
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown")
            })
            
            incident_list.append(incident_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 5,
    only_true_incidents: bool = True,
    include_root_cause_info: bool = True
) -> Dict[str, Any]:
    """
    Fetches a detailed summary of incidents including root cause information.
    Use this when you need more detail about specific incidents after reviewing the compact list.

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                         If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                       If not provided, defaults to the current time.
        limit (int): Maximum number of incidents to return (default: 5).
        only_true_incidents (bool): If True, only return events marked as true incidents (default: True).
        include_root_cause_info (bool): If True, include information about root cause availability (default: True).

    Note: All timestamps are in the Owner User Timezone. Display times using the
    "timezone" field from the response, never label as UTC.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
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



        # If start and end time are the same (e.g. user provided "2026-02-12" for both),
        # expand to cover the full day (00:00:00.000 to 23:59:59.999).
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            import datetime
            dt = datetime.datetime.fromtimestamp(start_time_ms / 1000, tz=datetime.timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)
            
            if settings.ENABLE_DEBUG_MESSAGES:
                logger.debug(f"Expanded equal start/end time to full day: {start_time_ms} - {end_time_ms}")

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
            timestamp_str = format_api_timestamp_corrected(incident["timestamp"], tz_name)
            
            summary = {
                "incident_id": len(incidents_summary) + 1,  # Simple ID for reference
                "timestamp": incident["timestamp"],
                "timestamp_human": timestamp_str,
                "projectDisplayName": incident.get("projectDisplayName", "Unknown"),
                "realProjectName": incident.get("projectName", "Unknown"),
                "instanceName": incident.get("instanceName", "Unknown"),
            }
            
            # Add metric name right after instanceName only if available
            if "rootCause" in incident and incident["rootCause"] and "metricName" in incident["rootCause"]:
                summary["metricName"] = incident["rootCause"]["metricName"]
            
            # Add remaining fields
            summary.update({
                "componentName": incident.get("componentName", "Unknown"),
                "patternName": incident.get("patternName", "Unknown"),
                "anomalyScore": incident.get("anomalyScore", 0),
                "status": incident.get("status", "unknown"),
                "isIncident": incident.get("isIncident", False),
                "has_raw_data": "rawData" in incident and incident["rawData"] is not None,
                "has_root_cause": incident.get('rootCauseResultInfo', {}).get('hasPrecedingEvent', False) or ("rootCause" in incident and incident["rootCause"] is not None),
            })
            
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
                        # "leadToIncident": incident["rootCauseResultInfo"].get("leadToIncident", False)
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
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
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
    incident_timestamp: str,
    instance_name: Optional[str] = None,
    pattern_id: Optional[str] = None,
    pattern_name: Optional[str] = None,
    include_root_cause: bool = True,
    fetch_rca_chain: bool = False,
    include_recommendations: bool = False
) -> Dict[str, Any]:
    """
    Fetches complete information about a specific incident, excluding raw data to keep response manageable.
    Use this after identifying a specific incident from the list or summary layers.

    Note - Important Policy:
    - Always fetch and display the **entire root cause analysis (RCA) chain**.  
    - The RCA chain must include **all available timestamps, project names, event details, and CDN (if available)**.  
    - The RCA chain must be displayed in **strict chronological order by event timestamp**, with no reordering or omission.  
    - RCA events should be displayed in **batches of 7-10 at a time**, always in order.  
    - After showing a batch, clearly indicate the **total RCA event count**, how many have been shown, and how many remain.  
    - Prompt the user if they want to see the remaining events.  

    Recommendations:
    - If the user requests recommendations, set `include_recommendations=True`.  
    - If available, include recommendations under the `recommendation` field and set `recommendation_available=True`.  
    - Recommendations may include suggested actions, remediation steps, or system insights.  
    - If no recommendations exist, return `recommendation_available=False`.  

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (str): The timestamp of the incident.
                                  Accepts: "2026-02-12T01:15:00", or 13-digit milliseconds.
        instance_name (str): Optional. Filter by specific instance name.
        pattern_id (str): Optional. Filter by specific pattern ID.
        pattern_name (str): Optional. Filter by specific pattern name.
        include_root_cause (bool): Whether to include detailed root cause information.
        fetch_rca_chain (bool): Whether to fetch the full root cause analysis chain (always set to True when user requests root cause or causal chain).
        include_recommendations (bool): Whether to include recommendations or remediation steps if available.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert any human-readable timestamp to InsightFinder fake-UTC ms
        try:
            timestamp_ms = convert_to_ms(incident_timestamp, "incident_timestamp", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if timestamp_ms is None:
            return {"status": "error", "message": "incident_timestamp is required"}
        
        # Use a 1-minute window around the incident timestamp
        window_ms = 1 * 60 * 1000  # 1 minute in milliseconds
        start_time = timestamp_ms - window_ms
        end_time = timestamp_ms + window_ms
        
        client = _get_api_client()
        incidents_response = await client._fetch_timeline_data(
            "incident",
            system_name,
            start_time,
            end_time
        )

        # Find the specific incident in the response
        incidents = incidents_response.get('data', [])
        
        # Filter only true incidents
        incidents = [i for i in incidents if i.get('isIncident', False)]
        
        incident_data = None
        
        # Check if all optional filters are None
        if instance_name is None and pattern_id is None and pattern_name is None:
            # Timestamp match at minute granularity (ignoring seconds and milliseconds)
            target_timestamp_minutes = timestamp_ms // 60000  # Convert to minutes
            for inc in incidents:
                incident_timestamp_minutes = inc.get('timestamp', 0) // 60000  # Convert to minutes
                if incident_timestamp_minutes == target_timestamp_minutes:
                    incident_data = inc
                    break
        else:
            # Filter by optional parameters within time window
            for inc in incidents:
                if inc.get('timestamp') >= start_time and inc.get('timestamp') <= end_time:
                    # Check all provided filters
                    match = True
                    
                    if instance_name is not None and inc.get('instanceName') != instance_name:
                        match = False
                    
                    if pattern_id is not None and inc.get('patternId') != pattern_id:
                        match = False
                    
                    if pattern_name is not None and inc.get('patternName') != pattern_name:
                        match = False
                    
                    if match:
                        incident_data = inc
                        break
            
            # If no match found with filters, return the first incident in the time window
            if incident_data is None and incidents:
                incident_data = incidents[0]
                
        if not incident_data:
            return {"status": "error", "message": "No incident found with the specified timestamp"}

        result_incident = incident_data.copy()  # For clarity in the result structure
        result_incident.pop('rawData', None)  # Remove raw data to keep response manageable
        result_incident.pop('rootCause', None)  # Remove root cause summary to avoid confusion
        result_incident.pop('rootCauseResultInfo', None)  # Remove root cause result info to avoid confusion
        result_incident.pop('rootCauseInfoKey', None)  # Remove root cause info key to avoid confusion
        result_incident.pop('incidentLLMKey', None)  # Remove incidentLLMKey to avoid confusion

        # Extract metric name if available in rootCause
        metric_name = None
        if "rootCause" in incident_data and incident_data["rootCause"] and "metricName" in incident_data["rootCause"]:
            metric_name = incident_data["rootCause"]["metricName"]

        result = {
            "metricName": metric_name,
            "incident": result_incident,
            "raw_data_available": True,  # Indicate that raw data can be fetched separately
            "root_cause_available": False,
            "root_cause_chain": None,
            "recommendation_available": False,
            "recommendation": None,
            "anomalyScore": incident_data.get("anomalyScore"),
            "status": incident_data.get("status"),
            "isIncident": incident_data.get("isIncident"),
            "active": incident_data.get("active"),
            "projectDisplayName": incident_data.get("projectDisplayName", "Unknown"),
            "realProjectName": incident_data.get("projectName", "Unknown")
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
                    if not (isinstance(node_list, list) and node_list and 'eventTimestamp' in node_list[0]):
                        continue
                    import json
                    unique_nodes = {}
                    for node in node_list:
                        # Format didPredictionTime and eventEndTimestamp if present
                        for ts_field in ('didPredictionTime', 'eventEndTimestamp', 'eventTimestamp'):
                            if ts_field in node:
                                node[ts_field] = format_timestamp_in_user_timezone(node[ts_field], tz_name)

                        # Replace sourceProjectName with sourceProjectDisplayName and remove the display name
                        if 'sourceProjectDisplayName' in node:
                            node['sourceProjectName'] = node['sourceProjectDisplayName']
                            node.pop('sourceProjectDisplayName', None)

                        # Parse and extract key fields from sourceDetail if present
                        nid = node.get('nid')
                        pattern_name = node.get('patternName')
                        if 'sourceDetail' in node and node['sourceDetail']:
                            import json
                            try:
                                detail_obj = json.loads(node['sourceDetail'])
                                if detail_obj:
                                    if detail_obj.get('nid'):
                                        nid = detail_obj['nid']
                                        node['nid'] = nid
                                    if detail_obj.get('patternName'):
                                        pattern_name = detail_obj['patternName']
                                        node['patternName'] = pattern_name
                                    if detail_obj.get('content'):
                                        content_str = detail_obj['content']
                                        import json as _json
                                        try:
                                            content = _json.loads(content_str) if isinstance(content_str, str) else content_str
                                        except Exception:
                                            content = content_str
                                        # Extract common fields if they exist
                                        common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time"]
                                        extracted_fields = {field: content[field] for field in common_fields if isinstance(content, dict) and field in content}
                                        if extracted_fields:
                                            node["key_fields"] = extracted_fields
                                node.pop('sourceDetail', None)  # Remove the original sourceDetail to reduce clutter
                            except Exception:
                                pass
                        # Build deduplication key
                        key = (nid, pattern_name, node.get('sourceInstanceName'), node.get('sourceProjectName'))
                        if key not in unique_nodes:
                            # Copy node to avoid mutating original
                            node_copy = dict(node)
                            unique_nodes[key] = node_copy
                    # Remove nid from each node to reduce clutter
                    deduped_nodes = list(unique_nodes.values())
                    # for n in deduped_nodes:
                    #     n.pop('nid', None)
                    chain_item['rcaNodeList'] = sorted(deduped_nodes, key=lambda n: n.get('eventTimestamp', 0))


                merged_nodes = merge_rca_chain(rca_chain)
                result["root_cause_chain"] = merged_nodes
                result["root_cause_chain_event_count"] = len(merged_nodes)  
                result['root_cause_available'] = True
                # include the count of events in the chain
                # result['root_cause_chain_event_count'] = sum(len(item.get('rcaNodeList', [])) for item in rca_chain)
                # result['root_cause_chain'] = rca_chain
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
    incident_timestamp: str,
    max_length: int = 5000
) -> Dict[str, Any]:
    """
    Fetches the raw data (logs, stack traces) for a specific incident.
    Use this only when you need to examine the actual error logs or stack traces.

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (str): The timestamp of the specific incident.
                                  Accepts: "2026-02-12T01:15:00", "2026-02-12", or 13-digit milliseconds.
        max_length (int): Maximum length of raw data to return (to prevent overwhelming the LLM).
    """
    # Security checks
    if not system_name or len(system_name) > 100:
        return {"status": "error", "message": "Invalid system_name"}
    
    # Limit max_length to prevent abuse
    max_length = min(max_length, 10000)
    
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert any human-readable timestamp to InsightFinder fake-UTC ms
        try:
            timestamp_ms = convert_to_ms(incident_timestamp, "incident_timestamp", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if timestamp_ms is None:
            return {"status": "error", "message": "incident_timestamp is required"}
        
        # Get incidents for a small time window around the specific timestamp
        start_time = timestamp_ms - (5 * 60 * 1000)  # 5 minutes before
        end_time = timestamp_ms + (5 * 60 * 1000)    # 5 minutes after

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
            if incident["timestamp"] == timestamp_ms:
                target_incident = incident
                break

        if not target_incident:
            return {"status": "error", "message": f"Incident with timestamp {timestamp_ms} not found"}

        raw_data = target_incident.get("rawData", "")
        if not raw_data:
            return {"status": "error", "message": "No raw data available for this incident"}

        # Truncate if too long
        if len(raw_data) > max_length:
            raw_data = raw_data[:max_length] + f"\n... [TRUNCATED - Full length: {len(target_incident['rawData'])} characters]"

        result = {
            "status": "success",
            "incident_timestamp": timestamp_ms,
            "timestamp_human": format_api_timestamp_corrected(timestamp_ms, tz_name),
            "projectName": target_incident.get("projectDisplayName"),
            "instanceName": target_incident.get("instanceName"),
        }
        
        # Add metric name right after instanceName only if available
        if "rootCause" in target_incident and target_incident["rootCause"] and "metricName" in target_incident["rootCause"]:
            result["metricName"] = target_incident["rootCause"]["metricName"]
        
        # Add remaining fields
        result.update({
            "componentName": target_incident.get("componentName"),
            "raw_data": raw_data,
            "raw_data_length": len(target_incident.get("rawData", "")),
            "truncated": len(target_incident.get("rawData", "")) > max_length
        })
        
        return result
        
    except Exception as e:
        error_message = f"Error in get_incident_raw_data: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 5: Statistics and analysis tools
@mcp_server.tool()
async def get_incidents_statistics(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Provides statistical analysis of incidents for a system over a time period.
    Use this to understand incident patterns, frequency, and impact.

    Args:
        system_name (str): The name of the system to analyze.
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
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
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches trace timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for traces, distributed tracing, or application performance data.

    Args:
        system_name (str): The name of the system to query for traces.
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                         If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                       If not provided, defaults to the current time.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches log anomaly timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for log anomalies, unusual log patterns, or log-based issues.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                         If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                       If not provided, defaults to the current time.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches deployment timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for deployments, releases, or change events.

    Args:
        system_name (str): The name of the system to query for deployments.
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                         If not provided, defaults to 24 hours ago.
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
                       If not provided, defaults to the current time.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
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
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    only_true_incidents: bool = True,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Fetches incidents specifically for a given project within a system.
    Use this tool when the user specifies both a system name and project name.
    
    Example usage:
    - "show me incidents for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get incidents after timestamp for project X in system Y"

    Args:
        system_name (str): The name of the system (e.g., "InsightFinder Demo System (APP)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time (str): Optional. The start of the time window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026"
        end_time (str): Optional. The end of the time window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026"
        only_true_incidents (bool): If True, only return events marked as true incidents
        limit (int): Maximum number of incidents to return (default: 20)
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Log input parameters for debugging
        logger.debug(
            "get_project_incidents called with system_name=%s, project_name=%s, start_time_ms=%s, end_time_ms=%s, only_true_incidents=%s, limit=%s",
            system_name, project_name, start_time_ms, end_time_ms, only_true_incidents, limit
        )
        if settings.ENABLE_DEBUG_MESSAGES:
            print(f"[DEBUG] get_project_incidents params: system_name={system_name}, project_name={project_name}, start_time_ms={start_time_ms}, end_time_ms={end_time_ms}, only_true_incidents={only_true_incidents}, limit={limit}", file=sys.stderr)

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
                "timestamp_human": format_api_timestamp_corrected(incident["timestamp"], tz_name),
                "project": incident.get("projectDisplayName", "Unknown"),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
            }
            
            # Add metric name right after instance only if available
            if "rootCause" in incident and incident["rootCause"] and "metricName" in incident["rootCause"]:
                incident_info["metricName"] = incident["rootCause"]["metricName"]
            
            # Add remaining fields
            incident_info.update({
                "pattern": incident.get("patternName", "Unknown"),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown"),
                "active": incident.get("active", False)
            })

            
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
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
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
    start_time: str,
    end_time: str,
) -> Dict[str, Any]:
    """
    Predicts future incidents for a system in a given time window.
    Uses the InsightFinder prediction API to fetch predicted incidents.
    This will include recommendations for each predicted incident if available.
    
    Note:
        The timestamp for each predicted incident is always taken from the top-level 'timestamp_prediction' field of the incident object.

    Args:
        system_name (str): The name of the system to predict incidents for.
        start_time (str): Start of the prediction window.
                         Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".
        end_time (str): End of the prediction window.
                       Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026".

    Returns:
        Dict[str, Any]: Prediction results, including recommendations if any are available.
    """
    try:
        # Security checks
        if not system_name or len(system_name) > 100:
            return {"status": "error", "message": "Invalid system_name"}

        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert human-readable timestamps to InsightFinder fake-UTC ms
        try:
            start_time_ms = _convert_timestamp_to_int(start_time, "start_time", tz_name)
            end_time_ms = _convert_timestamp_to_int(end_time, "end_time", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if start_time_ms is None or end_time_ms is None:
            return {"status": "error", "message": "start_time and end_time are required for predictions"}

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
            
        #     print(f"[DEBUG] Predicted incident timestamp: {incident.get('timestamp')} - {format_api_timestamp_corrected(incident.get('timestamp', tz_name))}", file=sys.stderr)


        incident_list = []
        for i, incident in enumerate(timeline_list):
            incident_info = {
                "id": i + 1,
                "timestamp_prediction": incident["predictionTime"],
                "timestamp_prediction_human": format_api_timestamp_corrected(incident["predictionTime"], tz_name),
                "timestamp_occurence_prediction": incident["predictionOccurenceTime"],
                "timestamp_occurence_prediction_human": format_api_timestamp_corrected(incident["predictionOccurenceTime"], tz_name),
                "project": incident.get("projectDisplayName", "Unknown"),
                "component": incident.get("componentName", "Unknown"),
                "instance": incident.get("instanceName", "Unknown"),
            }
            
            # Add metric name right after instance only if available
            if "rootCause" in incident and incident["rootCause"] and "metricName" in incident["rootCause"]:
                incident_info["metricName"] = incident["rootCause"]["metricName"]
            
            # Add remaining fields
            incident_info.update({
                "pattern": incident.get("patternName", "Unknown"),
                # "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown"),
                "active": incident.get("active", False)
            })

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
            "timezone": tz_name,
            "time_range": {
                "start": start_time_ms,
                "end": end_time_ms,
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
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

def merge_rca_chain(rca_chain: list) -> list:
    """
    Merge all rcaNodeList items into a single deduplicated list sorted by eventTimestamp.
    Deduplication is based on (sourceInstanceName, sourceProjectName, patternName, eventTimestamp).
    """
    unique_nodes = {}
    merged_nodes = []

    for chain_item in rca_chain:
        node_list = chain_item.get("rcaNodeList", [])
        for node in node_list:
            # Deduplication key
            key = (
                node.get("sourceInstanceName"),
                node.get("sourceProjectName"),
                node.get("patternName"),
                node.get("nid")
            )
            if key not in unique_nodes:
                unique_nodes[key] = node

    # Collect deduplicated nodes
    merged_nodes = list(unique_nodes.values())

    # Sort strictly by eventTimestamp (formatted string in owner timezone)
    from datetime import datetime
    def parse_ts(ts):
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return ts  # fallback, keep order as-is

    merged_nodes.sort(key=lambda n: parse_ts(n.get("eventTimestamp", "")))

    return merged_nodes
