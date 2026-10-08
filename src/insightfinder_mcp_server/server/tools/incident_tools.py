import json
import re
import sys
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

logger = logging.getLogger(__name__)
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
    parse_relative_date_keyword,
)
from .ui_url import build_systemrootcause_url
from .ari_report import ari_fields, ari_status_counts, strip_ari_fields, COMPLETED


# Layer 0: Ultra-compact incident overview (just counts and basic info)
@mcp_server.tool()
async def get_incidents_overview(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    project_name: Optional[str] = None,
    include_consolidated: bool = False
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of incidents - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration and comparisons.
    Use this tool when a user first asks about incidents to get a quick overview or to compare time periods.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
        system_name (str): The name of the system to query for incidents.
        start_time (Optional[Union[str, int]]): The start of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        end_time (Optional[Union[str, int]]): The end of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        project_name (str): Optional. Filter results to only include incidents from this specific project.
        include_consolidated (bool): If True, include counts of consolidated (dampened) incidents
            that were suppressed under primary incidents. Default is False.

    Returns:
        Dict with status and overview containing incident counts and basic statistics.
    """
    # Simple security checks
    if not system_name or len(system_name) > 100:
        return {"status": "error", "message": "Invalid system_name"}
    
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
        consolidated_data = result.get("consolidated_data", [])
        consolidated_index = _build_consolidated_index(consolidated_data)

        # Filter by project name if specified
        if project_name:
            incidents = [i for i in incidents if i.get("projectName", "").lower() == project_name.lower() or i.get("projectDisplayName", "").lower() == project_name.lower()]

        # primary_count is the number of primary incident objects; total uses count fields
        primary_count = len(incidents)
        consolidated_count = sum(c.get("count", 0) for c in consolidated_data)
        total_incidents = sum(i.get("count", 0) for i in incidents) + consolidated_count

        # Time range analysis (primaries only for first/last timestamps)
        if incidents:
            timestamps = [incident["timestamp"] for incident in incidents]
            first_incident = min(timestamps)
            last_incident = max(timestamps)
        else:
            first_incident = last_incident = None

        # Unique dimension counts across primary incidents
        unique_components = len(set(incident.get("componentName", "Unknown") for incident in incidents))
        unique_instances = len(set(incident.get("instanceName", "Unknown") for incident in incidents))
        unique_patterns = len(set(incident.get("patternName", "Unknown") for incident in incidents))
        unique_projects = len(set(incident.get("projectDisplayName", "Unknown") for incident in incidents))

        summary = {
            "total_incidents": total_incidents,
            "consolidated_incidents": primary_count,
            "suppressed_incidents": total_incidents - primary_count,
            "primaries_with_consolidation": sum(1 for i in incidents if i.get("relatedTimelineIdList")),
            "unique_components": unique_components,
            "unique_instances": unique_instances,
            "unique_patterns": unique_patterns,
            "unique_projects": unique_projects,
            "first_event": format_api_timestamp_corrected(first_incident, tz_name) if first_incident else None,
            "last_event": format_api_timestamp_corrected(last_incident, tz_name) if last_incident else None,
            "has_incidents": total_incidents > 0
        }

        # On-call ARI investigations of the primary incidents: counts by status, plus the
        # Completed ones with their report digest (most recent first) for summaries.
        ari_counts = ari_status_counts(incidents)
        if ari_counts:
            completed = []
            for inc in sorted(incidents, key=lambda x: x.get("timestamp", 0), reverse=True):
                fields = ari_fields(inc)
                if fields.get("ari_status") == COMPLETED and fields.get("ari_digest"):
                    when = inc.get("incidentTimestamp") or inc.get("timestamp")
                    completed.append({
                        "timestamp": when,
                        "timestamp_human": format_api_timestamp_corrected(when, tz_name),
                        "issue": _issue_label(inc, 160),
                        "projectDisplayName": inc.get("projectDisplayName", "Unknown"),
                        "component": inc.get("componentName", "Unknown"),
                        "instance": inc.get("instanceName", "Unknown"),
                        "pattern": inc.get("patternName", "Unknown"),
                        "ari_digest": fields["ari_digest"],
                    })
            summary["ari_investigations"] = {
                "by_status": ari_counts,
                "completed": completed[:10],
                "completed_total": len(completed),
            }

        if include_consolidated:
            # Tally flagDesc across all items in both timelineList and consolidatedTimelineList
            flag_counts: Dict[str, int] = {}
            for item in incidents:
                flag_desc = _get_flag_desc(item)
                flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
            for item in consolidated_data:
                flag_desc = _get_flag_desc(item)
                flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
            # Remaining = total_incidents - total object count; represents count-field excess with no known type
            excess = total_incidents - (len(incidents) + len(consolidated_data))
            if excess > 0:
                flag_counts["Instance level content similarity consolidation"] = flag_counts.get("Instance level content similarity consolidation", 0) + excess
            summary["consolidation_breakdown"] = flag_counts

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "summary": summary
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
    only_true_incidents: bool = True,
    include_consolidated: bool = False
) -> Dict[str, Any]:
    """
    Fetches a compact list of incidents with basic information only.
    Use this after getting the overview to see individual incidents without overwhelming detail.

    ⚠️ The `formatted_preview` field is the finished answer to "list the incidents": one line per
    incident (time, component, detected issue, ARI investigation status, ticket). Return it
    as-is; do not rewrite or shorten it.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

    ⚠️ RELATIVE DATE KEYWORDS: for "today", "yesterday", "this week", "last week", "this month"
    or "last month" pass the keyword itself as BOTH start_time and end_time ("today", "yesterday",
    "thisweek", "lastweek", "thismonth", "lastmonth") — do not omit them: with no start_time
    the window is the last 24 hours, which is not "today".

    Args:
        system_name (str): The name of the system to query for incidents.
        start_time (str): Optional. The start of the time window.
                Accepts:
                - Relative keywords: "today", "yesterday", "thisweek", "lastweek", "thismonth", "lastmonth"
                - "2026-02-12T11:05:00" (ISO timestamp with time), "2026-02-12", "02/12/2026"
                - If NOT provided, defaults to 24 hours ago from the current time.
                - If the user explicitly asks for "last 24 hours", DO NOT pass start_time or end_time. Leave both unset so the system uses the default 24-hour window.
                - If the user asks for rolling windows other than 24 hours (e.g., "last 48 hours", "last 72 hours", "last 7 days"), you MUST calculate and pass FULL ISO timestamps including BOTH date and time.
        end_time (str): Optional. The end of the time window.
                Accepts:
                - Relative keywords: same as start_time
                - "2026-02-12T11:05:00" (ISO timestamp with time), "2026-02-12", "02/12/2026"
                - If NOT provided, defaults to the current time.
                - For rolling windows (except "last 24 hours"), always include time precision when passing values. Do NOT pass date-only values for rolling ranges.
        limit (int): Maximum number of incidents to return (default: 10).
        only_true_incidents (bool): If True, only return events marked as true incidents. default is True.
        include_consolidated (bool): If True, each incident will include a list of consolidated
            (dampened/suppressed) incidents that were grouped under it. Default is False.

    Note: All timestamps are in the Owner User Timezone. Display times using the
    "timezone" field from the response, never label as UTC.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
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
        consolidated_data = result.get("consolidated_data", [])
        consolidated_index = _build_consolidated_index(consolidated_data)

        # Filter for true incidents if requested
        if only_true_incidents:
            incidents = [i for i in incidents if i.get("isIncident", False)]
        matched_count = len(incidents)

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
                "issue": _issue_label(incident, 160),
                "anomaly_score": round(incident.get("anomalyScore", 0), 2),
                "is_incident": incident.get("isIncident", False),
                "status": incident.get("status", "unknown")
            })

            snow = _extract_servicenow_info(incident)
            if snow:
                incident_info["servicenow_ticket"] = snow

            incident_info.update(ari_fields(incident))

            if include_consolidated:
                consolidated = _attach_consolidated(incident, consolidated_index, tz_name)
                incident_info["consolidated_incidents"] = consolidated
                incident_info["consolidated_count"] = len(consolidated)

            incident_list.append(incident_info)

        time_range = {
            "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
            "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
        }
        response = {
            "status": "success",
            "system_name": system_name,
            "formatted_preview": _render_incident_list(system_name, time_range, incident_list,
                                                       matched_count, only_true_incidents),
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit,
                "include_consolidated": include_consolidated
            },
            "time_range": time_range,
            "total_found": len(result["data"]),
            "returned_count": len(incident_list),
            "incidents": incident_list
        }
        if include_consolidated:
            response["total_consolidated_found"] = len(consolidated_data)
        return response

    except Exception as e:
        error_message = f"Error in get_incidents_list: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

def _render_incident_list(system_name: str, time_range: dict, incidents: list, matched: int,
                          only_true: bool) -> str:
    """get_incidents_list's finished markdown: one line per incident, newest first, in the
    daily summary's style (time, component, instance, project, detected issue), plus its ARI
    investigation status and ServiceNow ticket."""
    kind = "incidents" if only_true else "incident events"
    head = (f"**{matched:,} {kind} in {system_name}** — {time_range['start_human']} to "
            f"{time_range['end_human']}")
    if not incidents:
        return head + "\n\nNo incidents in this window."
    if len(incidents) < matched:
        head += f" (showing the {len(incidents)} most recent)"
    lines = [head + ":", ""]
    for inc in incidents:
        component, instance = inc.get("component"), inc.get("instance")
        where = component if instance in (None, "Unknown", component) else f"{component} (Instance: {instance})"
        line = (f"- [{inc['timestamp_human']}]: {where} in {inc['projectDisplayName']} project. "
                f"Detected issue: {inc['issue'].rstrip('.')}")
        pattern = str(inc.get("pattern") or "")
        if pattern and pattern != "Unknown" and pattern not in inc["issue"]:
            line += f" (pattern {pattern})"
        line += "."
        if inc.get("ari_status"):
            line += f" ARI investigation: {inc['ari_status']}."
        snow = inc.get("servicenow_ticket") or {}
        if snow.get("ticket_number"):
            number = snow["ticket_number"]
            line += (f" [ServiceNow: {number}]({snow['hyperlink']})." if snow.get("hyperlink")
                     else f" ServiceNow: {number}.")
        lines.append(line)
    if len(incidents) < matched:
        lines += ["", f"…and {matched - len(incidents):,} earlier; ask for more to see them."]
    return "\n".join(lines)


# Layer 2: Detailed incident summary (includes root cause summary but still manageable)
@mcp_server.tool()
async def get_incidents_summary(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 5,
    only_true_incidents: bool = True,
    include_root_cause_info: bool = True,
    include_consolidated: bool = False
) -> Dict[str, Any]:
    """
    Fetches a detailed summary of incidents including root cause information.
    Use this when you need more detail about specific incidents after reviewing the compact list.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
        include_consolidated (bool): If True, each incident will include a list of consolidated
            (dampened/suppressed) incidents grouped under it, with consolidation type and info. Default is False.

    Note: All timestamps are in the Owner User Timezone. Display times using the
    "timezone" field from the response, never label as UTC.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert string timestamps to integers if needed
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
        consolidated_data = result.get("consolidated_data", [])
        consolidated_index = _build_consolidated_index(consolidated_data)

        # Filter for true incidents if requested
        if only_true_incidents:
            incidents = [i for i in incidents if i.get("isIncident", False)]
        matched_count = len(incidents)

        # Sort by timestamp (most recent first) and limit
        incidents = sorted(incidents, key=lambda x: x["timestamp"], reverse=True)[:limit]

        # Extract detailed summary information
        incidents_summary = []
        for incident in incidents:
            timestamp_str = format_api_timestamp_corrected(incident["timestamp"], tz_name)

            summary = {
                "incident_id": len(incidents_summary) + 1,
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

                if "rootCauseResultInfo" in incident and incident["rootCauseResultInfo"]:
                    root_cause_info["result_info"] = {
                        "hasPrecedingEvent": incident["rootCauseResultInfo"].get("hasPrecedingEvent", False),
                        "hasTrailingEvent": incident["rootCauseResultInfo"].get("hasTrailingEvent", False),
                        "causedByChangeEvent": incident["rootCauseResultInfo"].get("causedByChangeEvent", False),
                    }

                if "rootCauseInfoKey" in incident and incident["rootCauseInfoKey"]:
                    root_cause_info["info_key"] = {
                        "projectName": incident["rootCauseInfoKey"].get("projectName"),
                        "instanceName": incident["rootCauseInfoKey"].get("instanceName"),
                        "incidentTimestamp": incident["rootCauseInfoKey"].get("incidentTimestamp")
                    }

                if root_cause_info:
                    summary["root_cause_info"] = root_cause_info

            snow = _extract_servicenow_info(incident)
            if snow:
                summary["servicenow_ticket"] = snow

            summary.update(ari_fields(incident))

            if include_consolidated:
                consolidated = _attach_consolidated(incident, consolidated_index, tz_name)
                summary["consolidated_incidents"] = consolidated
                summary["consolidated_count"] = len(consolidated)

            incidents_summary.append(summary)

        time_range = {
            "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
            "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
        }
        response = {
            "status": "success",
            "system_name": system_name,
            "formatted_preview": _render_incident_list(system_name, time_range, incident_list,
                                                       matched_count, only_true_incidents),
            "filters": {
                "only_true_incidents": only_true_incidents,
                "limit": limit,
                "include_consolidated": include_consolidated
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
        if include_consolidated:
            response["total_consolidated_found"] = len(consolidated_data)
        return response

    except Exception as e:
        error_message = f"Error in get_incidents_summary: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 3: Full incident details (without raw data)
_TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.I)


def _incident_ts_ms(value, tz_name: str):
    """convert_to_ms for an incident's time, also accepting a time without a date ("00:42",
    "12:11:02", "1:30pm"): users usually give only the time of a recent incident, and a model
    left to pick the date guesses (once, the docstring's example date). Resolved to the most
    recent such time in the owner's timezone — today, or yesterday if it has not come yet."""
    m = _TIME_ONLY_RE.match(str(value)) if value is not None else None
    if not m:
        return convert_to_ms(value, "incident_timestamp", tz_name)
    hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    ampm = (m.group(4) or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"incident_timestamp: invalid time '{value}'")
    from datetime import datetime as _dt, timedelta as _td
    from zoneinfo import ZoneInfo
    now = _dt.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    when = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if when > now:
        when -= _td(days=1)
    # owner wall clock as "fake UTC" epoch ms, as everywhere else
    return int(when.replace(tzinfo=timezone.utc).timestamp() * 1000)


ARI_REPORT_STATUSES = (COMPLETED, "Failed")  # an investigation that reached a report


async def _fetch_llm_result(client, incident_data: dict) -> Optional[Dict[str, Any]]:
    """The incident's LLM summary result (rca / ari_report / next_steps), or None."""
    incident_llm_key = incident_data.get('incidentLLMKey')
    if not incident_llm_key:
        return None
    root_cause_info = incident_data.get('rootCauseInfoKey')
    timestamp = root_cause_info.get('incidentTimestamp') \
        if root_cause_info and 'incidentTimestamp' in root_cause_info \
        else incident_data.get('timestamp')
    try:
        # incidentLLMKey carries the raw project and its owner: direct lookup (~0.2 s), with the
        # scan of every system only as a fallback.
        fast = await client.get_project_system(incident_llm_key.get('projectName', ''),
                                               incident_llm_key.get('userName', ''))
        if fast:
            system_id = fast[3]
        else:
            project_info = await client.get_customer_name_for_project(
                incident_llm_key.get('projectName', ''))
            system_id = project_info[4] if project_info else ''
        if not system_id:
            return None
        return await client.fetch_incident_llm_result(
            user_name=incident_llm_key.get('userName', ''),
            project_name=incident_llm_key.get('projectName', ''),
            instance_name=incident_llm_key.get('instanceName', ''),
            timestamp=timestamp,
            pattern_id=incident_llm_key.get('patternId', 0),
            system_name=system_id)
    except Exception as e:
        logger.warning(f"Failed to fetch incident LLM summary: {str(e)}")
        return None


async def _locate_incident(system_name: str, incident_timestamp: str,
                           instance_name: Optional[str] = None, pattern_id: Optional[str] = None,
                           pattern_name: Optional[str] = None) -> Dict[str, Any]:
    """Find one incident on the timeline around `incident_timestamp` (shared by
    get_incident_details and get_incident_ari_report). Returns {"status": "success", "tz_name",
    "system_name", "client", "incident"} or {"status": "error", "message"}."""
    # Resolve owner timezone for this system
    tz_name, system_name = await resolve_system_timezone(system_name)

    # Convert any human-readable timestamp to InsightFinder fake-UTC ms
    try:
        timestamp_ms = _incident_ts_ms(incident_timestamp, tz_name)
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
                
                # pattern_id arrives as a string from the model; the record's is an int
                if pattern_id is not None and str(inc.get('patternId')) != str(pattern_id):
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
    return {"status": "success", "tz_name": tz_name, "system_name": system_name,
            "client": client, "incident": incident_data}


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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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

    On-call ARI report:
    - When the user asks for the ARI report / ARI investigation of an incident, what ARI found or
      did, or the fix / pull request ARI made, use get_incident_ari_report instead: it returns the
      report ready to display, with its pull request and report links.
    - `ari_status` is the investigation's status ("Completed", "In Progress", "Awaiting Approval",
      "Failed", "Denied", "Skipped", "Not Configured"); absent when ARI never looked at it.
    - `ari_report` (with `ari_report_available=True`) is the full ARI report in markdown; show it
      in full, links included. It takes the place of the LLM next steps, so `recommendation` is
      left empty when a report exists.
    - `ari_digest` is the report's short overview (overview, confidence, next_action, actions).
    - With no ARI report, say so (with `ari_status` if present) and use the root cause
      (fetch_rca_chain=True) and recommendations (include_recommendations=True) instead.

    Args:
        system_name (str): The name of the system to query.
        incident_timestamp (str): The timestamp of the incident: "YYYY-MM-DDTHH:MM:SS", 13-digit
                                  milliseconds, or just the time ("12:11", "1:30pm") when the
                                  user gives no date — it then means the most recent such time
                                  (today, or yesterday if that time has not come yet). Never
                                  invent a date.
        instance_name (str): Optional. Filter by specific instance name.
        pattern_id (str): Optional. Filter by specific pattern ID.
        pattern_name (str): Optional. Filter by specific pattern name.
        include_root_cause (bool): Whether to include detailed root cause information.
        fetch_rca_chain (bool): Whether to fetch the full root cause analysis chain (always set to True when user requests root cause or causal chain).
        include_recommendations (bool): Whether to include recommendations or remediation steps if available.
    """
    try:
        located = await _locate_incident(system_name, incident_timestamp, instance_name,
                                         pattern_id, pattern_name)
        if located["status"] != "success":
            return located
        tz_name, system_name = located["tz_name"], located["system_name"]
        client, incident_data = located["client"], located["incident"]

        result_incident = incident_data.copy()  # For clarity in the result structure
        result_incident.pop('rawData', None)  # Remove raw data to keep response manageable
        result_incident.pop('rootCause', None)  # Remove root cause summary to avoid confusion
        result_incident.pop('rootCauseResultInfo', None)  # Remove root cause result info to avoid confusion
        result_incident.pop('rootCauseInfoKey', None)  # Remove root cause info key to avoid confusion
        result_incident.pop('incidentLLMKey', None)  # Remove incidentLLMKey to avoid confusion
        strip_ari_fields(result_incident)  # Normalised into ari_* below

        # Extract metric name if available in rootCause
        metric_name = None
        if "rootCause" in incident_data and incident_data["rootCause"] and "metricName" in incident_data["rootCause"]:
            metric_name = incident_data["rootCause"]["metricName"]

        snow = _extract_servicenow_info(incident_data)
        result = {
            "metricName": metric_name,
            "ui-url": await build_systemrootcause_url(client, incident_data, event_category="incident"),
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
            "realProjectName": incident_data.get("projectName", "Unknown"),
            "servicenow_ticket": snow,
            "ari_report_available": False,
            "ari_report": None,
        }
        result.update(ari_fields(incident_data))

        # The on-call ARI report and the LLM summary come from the same endpoint. Read it when an
        # investigation reached a report (Completed, or Failed with its failure report), or when
        # the RCA chain is asked for.
        wants_ari_report = result.get("ari_status") in ARI_REPORT_STATUSES
        llm_result = await _fetch_llm_result(client, incident_data) \
            if wants_ari_report or (include_root_cause and fetch_rca_chain) else None
        if llm_result and llm_result.get("ari_report"):
            result["ari_report_available"] = True
            result["ari_report"] = llm_result["ari_report"]

        # Check if root cause analysis is available and requested
        root_cause_info = incident_data.get('rootCauseInfoKey')
        if include_root_cause and fetch_rca_chain:
            # Try the LLM summary API first
            llm_summary = llm_result.get("rca") if llm_result else None
            if llm_summary:
                logger.info(f"LLM summary fetch successful {llm_summary}")

            if llm_summary:
                logger.info("RCA source: LLM summary API")
                result["root_cause_chain"] = llm_summary
                result["root_cause_available"] = True
                result["root_cause_chain_event_count"] = 1
            elif root_cause_info:
                # Fallback: fetch the structured RCA chain
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
                    logger.info("RCA source: fallback structured chain (%d events)", len(merged_nodes))
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
        
        # Fetch recommendations if requested and incident LLM key is available. An ARI report
        # replaces the LLM next steps, as on the incident page.
        if include_recommendations and not result["ari_report_available"] \
                and 'incidentLLMKey' in incident_data and incident_data['incidentLLMKey']:
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

_ISSUE_STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "at", "for", "to", "and", "or", "with", "about", "from",
    "incident", "incidents", "issue", "issues", "problem", "error", "errors", "ari", "report",
    "investigation", "what", "did", "do", "does", "show", "me", "find", "that", "this", "today",
    "yesterday", "system", "fix", "around", "please",
}
_MATCH_WINDOW_MS = 30 * 60 * 1000  # issue + time: candidates within 30 minutes of the time


def _issue_words(issue: str) -> list:
    words = re.findall(r"[a-z0-9]+", (issue or "").lower())
    return [w for w in words if len(w) > 1 and w not in _ISSUE_STOPWORDS]


def _issue_score(incident: dict, words: list) -> int:
    """How many of the description's words appear in the incident (pattern name, log line,
    component, instance, project); substring match, so "timeout" finds SocketTimeoutException."""
    raw = incident.get("rawData")
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw)
    hay = " ".join(str(v or "") for v in (
        incident.get("patternName"), raw, incident.get("componentName"),
        incident.get("instanceName"), incident.get("projectDisplayName"),
        incident.get("projectName"))).lower()
    return sum(1 for w in words if w in hay)


def _incident_time(incident: dict) -> int:
    return int(incident.get("incidentTimestamp") or incident.get("timestamp") or 0)


async def _match_incident(system_name: str, issue: Optional[str],
                          incident_timestamp: Optional[str], date: Optional[str],
                          instance_name: Optional[str] = None, pattern_id: Optional[str] = None,
                          pattern_name: Optional[str] = None) -> Dict[str, Any]:
    """Find ONE incident by description and/or time (see get_incident_ari_report). Returns the
    same shape as _locate_incident, or {"status": "success", "message": ...} listing the
    candidates when several fit and nothing tells them apart."""
    tz_name, system_name = await resolve_system_timezone(system_name)
    client = _get_api_client()
    center = None
    if incident_timestamp:
        try:
            center = _incident_ts_ms(incident_timestamp, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
    if center is not None:
        start, end = center - _MATCH_WINDOW_MS, center + _MATCH_WINDOW_MS
        where = f"within 30 minutes of {format_api_timestamp_corrected(center, tz_name)}"
    else:
        try:
            start, end = parse_time_parameters(date or "today", date or "today", tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        if start is None or end is None:
            start, end = get_time_range_ms(tz_name, 1)
        where = f"on {date}" if date else "today"

    resp = await client._fetch_timeline_data("incident", system_name, start, end)
    if resp.get("status") != "success":
        return resp
    incidents = [i for i in resp.get("data", []) if i.get("isIncident", False)]
    if instance_name:
        incidents = [i for i in incidents if i.get("instanceName") == instance_name]
    if pattern_id is not None:
        incidents = [i for i in incidents if str(i.get("patternId")) == str(pattern_id)]
    if pattern_name:
        incidents = [i for i in incidents if i.get("patternName") == pattern_name]

    words = _issue_words(issue)
    if words:
        scored = [(_issue_score(i, words), i) for i in incidents]
        best = max((sc for sc, _ in scored), default=0)
        incidents = [i for sc, i in scored if sc == best and sc > 0]
    if not incidents:
        what = f"matching \"{issue}\" " if words else ""
        return {"status": "success", "system_name": system_name, "ari_report_available": False,
                "message": f"No incident {what}found in {system_name} {where}."}

    if center is not None:  # the time breaks ties
        incidents.sort(key=lambda i: abs(_incident_time(i) - center))
        return {"status": "success", "tz_name": tz_name, "system_name": system_name,
                "client": client, "incident": incidents[0]}
    if len(incidents) == 1:
        return {"status": "success", "tz_name": tz_name, "system_name": system_name,
                "client": client, "incident": incidents[0]}

    # Several fit and nothing tells them apart: list them, never guess.
    lines = []
    for inc in sorted(incidents, key=_incident_time)[:10]:
        fields = ari_fields(inc)
        has_pr = any(a.get("url") for a in (fields.get("ari_digest") or {}).get("actions") or [])
        ari = fields.get("ari_status") or "not investigated"
        lines.append(f"- {format_api_timestamp_corrected(_incident_time(inc), tz_name)}: "
                     f"{_issue_label(inc, 160)} — {inc.get('componentName', 'Unknown')} "
                     f"(instance {inc.get('instanceName', 'Unknown')}); ARI: {ari}"
                     + ("; opened a pull request" if has_pr else ""))
    more = f"\n…and {len(incidents) - 10} more." if len(incidents) > 10 else ""
    return {"status": "success", "system_name": system_name, "ari_report_available": False,
            "message": (f"{len(incidents)} incidents in {system_name} {where} match "
                        f"\"{issue}\". Which one do you mean? Reply with its time.\n"
                        + "\n".join(lines) + more)}


# The keys UIE's daily summary reads a JSON log line's message from, in its order
# (MCPService.JSON_SUMMARY_CANDIDATE_KEYS), so both show the same "Detected issue".
_RAW_MESSAGE_KEYS = ("summary", "message", "msg", "description", "reason", "error", "log")


def _raw_text(raw) -> str:
    """An incident's raw data as text; a JSON log line ({"error": "..."}) gives its message."""
    data = raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return raw
    if isinstance(data, dict):
        for key in _RAW_MESSAGE_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(data)
    if isinstance(data, list):
        return json.dumps(data)
    return str(data or "")


def _issue_label(incident: dict, limit: int = 300) -> str:
    """What the incident is about: its pattern name, or, when the pattern is unnamed (log
    incidents often carry just the pattern id as name), the first line of its raw data — the
    same "Detected issue" the daily summary shows."""
    name = str(incident.get("patternName") or "").strip()
    if name and not name.isdigit():
        return name
    raw = _raw_text(incident.get("rawData"))
    first = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    if first:
        return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0] + "…"
    return f"pattern {incident.get('patternId')}" if incident.get("patternId") is not None else "incident"


_RCA_CHAIN_SHOWN = 10


async def _no_ari_report_answer(base: dict, incident: dict, system_name: str, what: str,
                                issue: str, status: Optional[str],
                                page_url: Optional[str]) -> Dict[str, Any]:
    """The answer when an incident has no ARI report — not every system has on-call ARI enabled,
    and the agent stops on get_incident_ari_report (return_direct), so this must be complete on
    its own. Falls back to what get_incident_details finds: InsightFinder's LLM root cause, else
    the structured root-cause chain, plus its recommended next steps."""
    if status:
        reason = f"ARI's investigation of this incident is {status}"
    else:
        reason = "ARI has not investigated it (on-call ARI may not be enabled for this system)"
    lines = [f"No ARI report for the incident on {what} ({issue}): {reason}."]

    details = {}
    try:
        details = await get_incident_details(
            system_name, str(incident.get("timestamp")),
            instance_name=incident.get("instanceName"),
            pattern_id=str(incident.get("patternId")) if incident.get("patternId") is not None else None,
            include_root_cause=True, fetch_rca_chain=True, include_recommendations=True)
    except Exception as e:
        logger.warning(f"get_incident_ari_report: root-cause fallback failed: {e}")

    chain = details.get("root_cause_chain") if isinstance(details, dict) else None
    if isinstance(chain, str) and chain.strip():
        lines.append(f"**Root cause (InsightFinder analysis):**\n{chain.strip()}")
    elif isinstance(chain, list) and chain:
        events = []
        for n in chain[:_RCA_CHAIN_SHOWN]:
            if not isinstance(n, dict):
                continue
            what_happened = n.get("patternName") or n.get("metricName") or n.get("type") or "event"
            events.append(f"- {n.get('eventTimestamp', '')} — {n.get('sourceProjectName', '')} / "
                          f"{n.get('sourceInstanceName', '')}: {what_happened}")
        more = len(chain) - _RCA_CHAIN_SHOWN
        lines.append(f"**Root-cause chain (InsightFinder, {len(chain)} events):**\n" + "\n".join(events)
                     + (f"\n- …and {more} more; ask for the incident's full root-cause chain."
                        if more > 0 else ""))
    recommendation = details.get("recommendation") if isinstance(details, dict) else None
    if isinstance(recommendation, str) and recommendation.strip():
        lines.append(f"**Recommended next steps (InsightFinder):**\n{recommendation.strip()}")
    if len(lines) == 1:
        lines.append("InsightFinder has no root-cause analysis for this incident yet either.")

    citations = []
    if page_url:
        lines.append(f"[View incident in InsightFinder]({page_url})")
        citations.append({"source_type": "if_anomaly_detection", "format": "linked",
                          "label": f"{system_name} — Incident", "url": page_url})
    # A finished answer: shown as-is, so nothing is added to it (e.g. offers ARI cannot keep).
    return {**base, "formatted_preview": "\n\n".join(lines), "citations": citations}


@mcp_server.tool()
async def get_incident_ari_report(
    system_name: str,
    incident_timestamp: Optional[str] = None,
    issue: Optional[str] = None,
    date: Optional[str] = None,
    instance_name: Optional[str] = None,
    pattern_id: Optional[str] = None,
    pattern_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns the on-call ARI (Autonomous Reliability Insights) investigation report of ONE
    incident, ready to display: what ARI found, its confidence, what it did (pull requests,
    branches, tickets, with links), the next action, and a link to the full report.

    **Use this tool when the user asks for:** "the ARI report for <incident>", "what did ARI find /
    do about <incident>", "the fix / PR ARI made for <incident>", "ARI investigation of
    <incident>".
    **Not for** root cause / RCA / "why did it happen" / causal-chain questions that do not ask
    about ARI — use get_incident_details with fetch_rca_chain=True for those.

    Identify the incident by its time, by a description, or both — call this tool directly, do
    not look the incident up first:
    - `incident_timestamp` when the user gives a time ("the incident at 13:28").
    - `issue` with the user's own words for it ("LLM request timeout", "ServiceNow DNS failure");
      matched against the incident's pattern name, log line, component and project.
    - both, for "the LLM timeout incident around 1:30pm" (matched within 30 minutes of the time).
    - `date` (default today) is the day searched when only `issue` is given.
    When several incidents fit, the result lists them and asks which one; relay that question.

    ⚠️ When `formatted_preview` is present it is the complete answer: return it as-is, do not
    rewrite or summarise it (the links must reach the user), and do not call get_incident_details
    or any other tool for this incident. Only when there is no report (`ari_report_available` is
    false; the `message` says why) may you offer get_incident_details with fetch_rca_chain=True.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day without a year, default to 2026.

    Args:
        system_name: The system the incident belongs to.
        incident_timestamp: Optional. The incident's time: "YYYY-MM-DDTHH:MM:SS", 13-digit ms, or
            just the time ("00:42", "1:30pm") when the user gives no date (most recent such time;
            never invent a date).
        issue: Optional. The user's description of the incident.
        date: Optional. The day to search when only `issue` is given ("2026-10-07", "yesterday").
        instance_name: Optional. The incident's instance.
        pattern_id: Optional. The incident's pattern id.
        pattern_name: Optional. The incident's pattern name.
    """
    try:
        if not incident_timestamp and not (issue or "").strip():
            return {"status": "error",
                    "message": "Give the incident's time (incident_timestamp), a description "
                               "(issue), or both."}
        if incident_timestamp and not (issue or "").strip():
            located = await _locate_incident(system_name, incident_timestamp, instance_name,
                                             pattern_id, pattern_name)
        else:
            located = await _match_incident(system_name, issue, incident_timestamp, date,
                                             instance_name, pattern_id, pattern_name)
        if located["status"] != "success" or "incident" not in located:
            return located  # an error, or the "which one?" / "no match" message
        tz_name, system_name = located["tz_name"], located["system_name"]
        client, incident = located["client"], located["incident"]

        fields = ari_fields(incident)
        status = fields.get("ari_status")
        digest = fields.get("ari_digest") or {}
        when = format_api_timestamp_corrected(
            incident.get("incidentTimestamp") or incident.get("timestamp"), tz_name)
        what = (f"{incident.get('componentName', 'Unknown')} "
                f"(instance {incident.get('instanceName', 'Unknown')}, project "
                f"{incident.get('projectDisplayName', incident.get('projectName', 'Unknown'))}) at {when}")
        issue = _issue_label(incident)
        base = {"status": "success", "system_name": system_name, "incident_timestamp": when,
                "ari_status": status, "ari_report_available": False}

        # A report exists only for an investigation that reached one; otherwise the fallback
        # (get_incident_details) reads the root cause itself.
        llm_result = await _fetch_llm_result(client, incident) \
            if status in ARI_REPORT_STATUSES else None
        report = (llm_result or {}).get("ari_report")
        page_url = await build_systemrootcause_url(client, incident, event_category="incident")
        if not report:
            return await _no_ari_report_answer(base, incident, system_name, what, issue, status,
                                               page_url)

        report_url = f"{page_url}&eventRootCauseDetails=true" if page_url else None
        actions = [a for a in digest.get("actions") or [] if a.get("url")]

        conf = digest.get("confidence")
        lines = [f"## ARI report — {what}",
                 f"**Detected issue:** {issue}",
                 f"**Status:** {status}" + (f" · **Confidence:** {conf}" if conf else ""),
                 report.strip()]
        # The digest's actions come from what the agents returned; the report prose may not
        # carry their links.
        missing = [a for a in actions if a["url"] not in report]
        if missing:
            lines.append("**Actions taken:** " + ", ".join(
                f"[{a.get('title') or a.get('type') or 'link'}]({a['url']})" for a in missing))
        if report_url:
            lines.append(f"[View full ARI report]({report_url})")

        citations = []
        if report_url:
            citations.append({"source_type": "if_anomaly_detection", "format": "linked",
                              "label": f"{system_name} — ARI report", "url": report_url})
        for a in actions:
            citations.append({"source_type": "code_repo", "format": "linked",
                              "label": a.get("title") or a.get("type") or "Pull request",
                              "url": a["url"]})
        # No "ui-url": the report link above is the page link, and a ui-url would be appended
        # again as "View in InsightFinder UI".
        return {**base, "ari_report_available": True, "ari_digest": digest or None,
                "formatted_preview": "\n\n".join(lines), "citations": citations}
    except Exception as e:
        error_message = f"Error in get_incident_ari_report: {str(e)}"
        logger.error(error_message, exc_info=True)
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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            timestamp_ms = _incident_ts_ms(incident_timestamp, tz_name)
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
    include_consolidated: bool = False
) -> Dict[str, Any]:
    """
    Provides statistical analysis of incidents for a system over a time period.
    Use this tool to understand incident patterns, frequency, and impact across components.
    Ideal for comparing incidents between time periods.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
        include_consolidated (bool): If True, also compute statistics over consolidated
            (dampened/suppressed) incidents and include a consolidation breakdown. Default is False.

    Returns:
        Statistical breakdown with top affected components, instances, patterns, and projects.
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
        consolidated_data = result.get("consolidated_data", [])

        # primary_count is the number of primary incident objects; total uses count fields
        primary_count = len(incidents)
        referenced_consolidated_count = sum(c.get("count", 0) for c in consolidated_data)
        total_incident_count = sum(i.get("count", 0) for i in incidents) + referenced_consolidated_count

        components: Dict[str, int] = {}
        instances: Dict[str, int] = {}
        patterns: Dict[str, int] = {}
        projects: Dict[str, int] = {}

        for incident in incidents:
            component = incident.get("componentName", "Unknown")
            components[component] = components.get(component, 0) + 1
            instance = incident.get("instanceName", "Unknown")
            instances[instance] = instances.get(instance, 0) + 1
            pattern = incident.get("patternName", "Unknown")
            patterns[pattern] = patterns.get(pattern, 0) + 1
            project = incident.get("projectDisplayName", "Unknown")
            projects[project] = projects.get(project, 0) + 1

        statistics: Dict[str, Any] = {
            "total_incidents": total_incident_count,
            "consolidated_incidents": primary_count,
            "suppressed_incidents": total_incident_count - primary_count,
            "top_affected_components": dict(sorted(components.items(), key=lambda x: x[1], reverse=True)[:10]),
            "top_affected_instances": dict(sorted(instances.items(), key=lambda x: x[1], reverse=True)[:10]),
            "top_patterns": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:10]),
            "top_affected_projects": dict(sorted(projects.items(), key=lambda x: x[1], reverse=True)[:10])
        }

        if include_consolidated:
            c_components: Dict[str, int] = {}
            c_instances: Dict[str, int] = {}
            c_patterns: Dict[str, int] = {}
            c_projects: Dict[str, int] = {}
            flag_counts: Dict[str, int] = {}

            # Tally flagDesc across all items in both timelineList and consolidatedTimelineList
            for item in incidents:
                flag_desc = _get_flag_desc(item)
                flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
            for item in consolidated_data:
                c_components[item.get("componentName", "Unknown")] = c_components.get(item.get("componentName", "Unknown"), 0) + 1
                c_instances[item.get("instanceName", "Unknown")] = c_instances.get(item.get("instanceName", "Unknown"), 0) + 1
                c_patterns[item.get("patternName", "Unknown")] = c_patterns.get(item.get("patternName", "Unknown"), 0) + 1
                c_projects[item.get("projectDisplayName", "Unknown")] = c_projects.get(item.get("projectDisplayName", "Unknown"), 0) + 1
                flag_desc = _get_flag_desc(item)
                flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
            # Remaining = total_incidents - total object count; represents count-field excess with no known type
            excess = total_incident_count - (len(incidents) + len(consolidated_data))
            if excess > 0:
                flag_counts["Instance level content similarity consolidation"] = flag_counts.get("Instance level content similarity consolidation", 0) + excess

            statistics["consolidated_breakdown"] = {
                "total_suppressed": referenced_consolidated_count,
                "consolidation_type_breakdown": flag_counts,
                "top_affected_components": dict(sorted(c_components.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_instances": dict(sorted(c_instances.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_patterns": dict(sorted(c_patterns.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_projects": dict(sorted(c_projects.items(), key=lambda x: x[1], reverse=True)[:10])
            }

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "statistics": statistics
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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
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
    Summarizes ALL log anomalies for a system in a time range: total, per-project counts, a
    pattern digest (user-named patterns with counts, plus one description of the unnamed bucket)
    and the 20 most recent anomalies. Every record in the range is retrieved via the paged API
    and aggregated server-side, so the result stays small on any system.
    Use this tool when a user asks for log anomalies, unusual log patterns, or log-based issues.

    A complete named-pattern table is appended to the final answer automatically (verbatim_markdown);
    do not reproduce it. For the individual anomalies / raw log lines of one pattern or project,
    use get_project_log_anomalies with project_name (and pattern_name).

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        from ..progress import report_progress
        from .get_time import resolve_system_identity, format_timestamp_in_user_timezone
        from .log_anomaly_tools import build_pattern_summary, _summary_for_model

        api_client = _get_api_client()
        # Preferred: paged external API (every record, no cap). Fallback: unpaged timeline
        # (capped at 5000 records / 10 MB).
        report_progress(f"Resolving system {system_name}", stage="resolve")
        identity = await resolve_system_identity(system_name)
        result, data_source = None, "paged"
        if identity.get("system_id") and identity.get("owner"):
            result = await api_client.get_loganomaly_all(
                customer_name=identity["owner"],
                system_id=identity["system_id"],
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if result.get("status") != "success":
                result = None
        if result is None:
            data_source = "unpaged"
            result = await api_client.get_loganomaly(
                system_name=system_name,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
        if result.get("status") != "success":
            return result

        anomalies = result.get("data") or []
        report_progress(f"Analyzing {len(anomalies):,} log anomalies into patterns",
                        current=len(anomalies), total=len(anomalies), stage="analyze")

        date_label = format_timestamp_in_user_timezone(start_time_ms, tz_name)[:10]
        end_label = format_timestamp_in_user_timezone(end_time_ms, tz_name)[:10]
        if end_label != date_label:
            date_label = f"{date_label} to {end_label}"
        summary = build_pattern_summary(anomalies, tz_name, system_name, "all projects", date_label)

        by_project: Dict[str, int] = {}
        for a in anomalies:
            p = a.get("projectDisplayName") or a.get("projectName") or "Unknown"
            by_project[p] = by_project.get(p, 0) + 1
        recent = sorted(anomalies, key=lambda a: a.get("timestamp", 0) or 0, reverse=True)[:20]

        return {
            "status": "success",
            "system_name": system_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name),
            },
            "total_anomalies": len(anomalies),
            "data_source": data_source,
            "coverage": ("complete: every log anomaly in the time range was retrieved"
                         if data_source == "paged" else
                         "may be partial: unpaged API capped at 5000 records"),
            "anomalies_by_project": dict(sorted(by_project.items(), key=lambda kv: -kv[1])),
            "pattern_summary": _summary_for_model(summary),
            "verbatim_markdown": summary.get("verbatim_markdown", ""),
            "most_recent": [{
                "timestamp_human": format_timestamp_in_user_timezone(a.get("timestamp", 0) or 0, tz_name),
                "project": a.get("projectDisplayName") or a.get("projectName") or "Unknown",
                "component": a.get("componentName", "Unknown"),
                "instance": a.get("instanceName", "Unknown"),
                "pattern": a.get("patternName", "Unknown"),
                "anomaly_score": round(a.get("anomalyScore", 0) or 0, 2),
            } for a in recent],
        }

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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
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
                "issue": _issue_label(incident, 160),
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

            snow = _extract_servicenow_info(incident)
            if snow:
                incident_info["servicenow_ticket"] = snow

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

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

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
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
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

            snow = _extract_servicenow_info(incident)
            if snow:
                incident_info["servicenow_ticket"] = snow

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

@mcp_server.tool()
async def get_consolidated_incidents_report(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    consolidation_type: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Returns a focused report on consolidated (dampened/suppressed) incidents for a system.
    Use this tool when the user wants to understand noise reduction, suppression patterns,
    or asks questions like:
    - "show me the consolidated incidents last week vs this week"
    - "how many incidents were suppressed before June 11 vs after June 18"
    - "what got consolidated under each primary incident today"

    Each consolidated incident carries a consolidation_type derived from the API's flagDesc field,
    e.g. "COMPONENT_CONTENT_SIMILARITY_CONSOLIDATION", "INSTANCE_DAMPENING", etc.

    ⚠️ DEFAULT TIME RANGE: If no start_time or end_time is provided, defaults to TODAY
    (midnight to end of day in the system's timezone). Do NOT pass explicit dates unless
    the user specifies a different time period.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16", "March 5") without a year, always default to year 2026.

    ⚠️ RELATIVE DATE KEYWORDS SUPPORTED: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"

    Args:
        system_name (str): The name of the system to query.
        start_time (str): Optional start of the time window. Leave empty to default to today.
        end_time (str): Optional end of the time window. Leave empty to default to today.
        consolidation_type (str): Optional. Case-insensitive substring match against the flagDesc
            value, e.g. "CONTENT_SIMILARITY" to filter only content-based consolidations.
            Leave empty to return all types.
        limit (int): Maximum number of consolidated incidents to return (default: 50).

    Returns:
        Dict with consolidated incident list, type breakdown, and the primary incidents they belong to.
    """
    try:
        tz_name, system_name = await resolve_system_timezone(system_name)

        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = parse_relative_date_keyword("today", tz_name)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            import datetime
            dt = datetime.datetime.fromtimestamp(start_time_ms / 1000, tz=datetime.timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        if start_time_ms is None or end_time_ms is None:
            return {"status": "error", "message": "Could not determine time range."}

        api_client = _get_api_client()
        result = await api_client.get_incidents(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        primary_incidents = result["data"]
        consolidated_data = result.get("consolidated_data", [])
        consolidated_index = _build_consolidated_index(consolidated_data)

        # Only consider consolidated incidents that are actually referenced by a primary
        # via relatedTimelineIdList — those are the real suppressed incidents.
        id_to_primary: Dict[int, dict] = {}
        for primary in primary_incidents:
            for rid in primary.get("relatedTimelineIdList", []):
                id_to_primary[rid] = primary

        referenced_consolidated_count = sum(c.get("count", 0) for c in consolidated_data)

        # Build report entries from referenced consolidated incidents only
        report_entries = []
        flag_counts: Dict[str, int] = {}

        # Tally flagDesc across all items in both timelineList and consolidatedTimelineList
        for item in primary_incidents:
            flag_desc = _get_flag_desc(item)
            flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
        for item in consolidated_data:
            flag_desc = _get_flag_desc(item)
            flag_counts[flag_desc] = flag_counts.get(flag_desc, 0) + 1
        # Remaining = total_incidents - total object count; represents count-field excess with no known type
        total_incidents_val = sum(i.get("count", 0) for i in primary_incidents) + referenced_consolidated_count
        excess = total_incidents_val - (len(primary_incidents) + len(consolidated_data))
        if excess > 0:
            flag_counts["Instance level content similarity consolidation"] = flag_counts.get("Instance level content similarity consolidation", 0) + excess

        for rid, primary in id_to_primary.items():
            c = consolidated_index.get(rid)
            if not c:
                continue
            flag_desc = _get_flag_desc(c)
            # Filter by consolidation_type if provided — match against flagDesc (case-insensitive)
            if consolidation_type and consolidation_type.upper() not in flag_desc.upper():
                continue

            entry = _summarize_consolidated(c, tz_name)
            entry["primary_incident"] = {
                "instance": primary.get("instanceName", "Unknown"),
                "component": primary.get("componentName", "Unknown"),
                "timestamp": primary.get("timestamp"),
                "timestamp_human": format_api_timestamp_corrected(primary.get("timestamp", 0), tz_name),
                "project": primary.get("projectDisplayName", "Unknown"),
                "anomaly_score": round(primary.get("anomalyScore", 0), 2),
            }
            report_entries.append(entry)

        # Sort by timestamp descending, apply limit
        report_entries.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        report_entries = report_entries[:limit]

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "filters": {
                "consolidation_type": consolidation_type,
                "limit": limit
            },
            "summary": {
                "consolidated_incidents": len(primary_incidents),
                "total_incidents": total_incidents_val,
                "suppressed_incidents": total_incidents_val - len(primary_incidents),
                "consolidation_type_breakdown": flag_counts,
                "suppression_ratio": round((total_incidents_val - len(primary_incidents)) / max(len(primary_incidents), 1), 2)
            },
            "returned_count": len(report_entries),
            "consolidated_incidents": report_entries
        }

    except Exception as e:
        error_message = f"Error in get_consolidated_incidents_report: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}


def _get_flag_desc(item: dict) -> str:
    """Return the effective consolidation flagDesc for tallying.

    Consolidations that span multiple projects (isCrossProject=True) are
    broken out into their own "Cross Datasource Consolidation" category
    regardless of their underlying flagDesc.
    """
    dampening = item.get("dampeningFlagInfo") or {}
    flag_desc = dampening.get("flagDesc", "") or "Instance level content similarity consolidation"
    if dampening.get("isCrossProject"):
        return "Cross datasource consolidation"
    return flag_desc


def _build_consolidated_index(consolidated_data: list) -> dict:
    """Build a dict mapping incident id -> consolidated incident record."""
    return {item["id"]: item for item in consolidated_data if "id" in item}


def _summarize_consolidated(consolidated_incident: dict, tz_name: str) -> dict:
    """Return a compact summary of a consolidated incident for embedding in a parent."""
    dampening = consolidated_incident.get("dampeningFlagInfo", {})
    summary = {
        "id": consolidated_incident.get("id"),
        "instance": consolidated_incident.get("instanceName", "Unknown"),
        "component": consolidated_incident.get("componentName", "Unknown"),
        "timestamp": consolidated_incident.get("timestamp"),
        "timestamp_human": format_api_timestamp_corrected(consolidated_incident.get("timestamp", 0), tz_name),
        "anomaly_score": round(consolidated_incident.get("anomalyScore", 0), 2),
        "pattern": consolidated_incident.get("patternName", "Unknown"),
        "project": consolidated_incident.get("projectDisplayName", "Unknown"),
        "consolidation_type": _get_flag_desc(consolidated_incident),
        "consolidation_info": dampening.get("info", ""),
    }
    snow = _extract_servicenow_info(consolidated_incident)
    if snow:
        summary["servicenow_ticket"] = snow
    return summary


def _attach_consolidated(incident: dict, consolidated_index: dict, tz_name: str) -> list:
    """Return consolidated incident summaries for a primary incident."""
    related_ids = incident.get("relatedTimelineIdList", [])
    result = []
    for rid in related_ids:
        consolidated = consolidated_index.get(rid)
        if consolidated:
            result.append(_summarize_consolidated(consolidated, tz_name))
    return result


def _extract_servicenow_info(incident: dict) -> Optional[dict]:
    """Extract ServiceNow ticket number and hyperlink from an incident, if present."""
    snow = incident.get("serviceNowTimelineInfo")
    if not snow:
        return None
    result = {}
    if snow.get("number"):
        result["ticket_number"] = snow["number"]
    if snow.get("hyperLink"):
        result["hyperlink"] = snow["hyperLink"]
    return result or None


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
    Also transforms 'probability' field to 'confidenceScore' for consistency.
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

    # Transform 'probability' to 'confidenceScore' in merged nodes
    for node in merged_nodes:
        if "probability" in node:
            node["confidenceScore"] = node.pop("probability")

    return merged_nodes
