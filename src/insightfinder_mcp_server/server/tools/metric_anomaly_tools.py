"""
Streamlined metric anomaly tools for the InsightFinder MCP server.

This module provides a focused approach for exploring metric anomalies:
- Layer 0: Ultra-compact overview (get_metric_anomalies_overview)
- Layer 1: Enhanced list with detailed information (get_metric_anomalies_list) 
- Layer 2: Statistics and analysis (get_metric_anomalies_statistics)
- Additional: Simple wrapper (fetch_metric_anomalies) and today's anomalies (get_today_metric_anomalies)
- Project-specific: Project-filtered anomalies (get_project_metric_anomalies)

Each layer provides increasingly detailed information while maintaining LLM-friendly,
structured outputs optimized for analysis and reasoning. All tools now support optional
project_name filtering to focus on specific projects within a system.
"""

import asyncio
import json
import logging
import sys
from typing import Dict, Any, List, Optional, Union
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
)

logger = logging.getLogger(__name__)

# ============================================================================
# LAYER 0: ULTRA-COMPACT OVERVIEW
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_overview(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Layer 0: Ultra-compact overview of metric anomalies.
    
    Provides the most condensed view possible - just essential counts and high-level patterns.
    Perfect for initial assessment and determining if deeper investigation is needed.
    
    ⚠️ NOTE FOR LLMs: If displaying individual anomalies from this data, always include project names.
    Each anomaly contains projectName and projectDisplayName fields.
    
    Args:
        system_name: Name of the system to query
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        project_name: Optional project name to filter results (if not provided, returns all projects)
        
    Returns:
        Dict containing ultra-compact overview with status, summary stats, and key insights
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
        
        if settings.ENABLE_DEBUG_MESSAGES:
            logger.debug("Using time range: %s to %s", start_time_ms, end_time_ms)
        
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if raw_data.get("status") != "success":
            return raw_data

        # Use the same pattern as incident tools - data is in result["data"]
        anomalies = raw_data.get("data", [])
        
        # Filter by project name if specified
        if project_name:
            # anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName") == project_name]
            anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName", "").lower() == project_name.lower() or anomaly.get("projectDisplayName", "").lower() == project_name.lower()]
        
        if not anomalies:
            return {
                "status": "success",
                "message": "No metric anomalies found in the specified time range",
                "summary": {
                    "total_anomalies": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        anomalies = raw_data["data"]
        
        # Extract key metrics
        total_anomalies = len(anomalies)
        
        
        # Component and pattern analysis
        components = set()
        instances = set()
        patterns = set()
        projects = set()
        metric_types = set()
        zones = set()
        
        for anomaly in anomalies:
            if anomaly.get("componentName"):
                components.add(anomaly["componentName"])
            if anomaly.get("instanceName"):
                instances.add(anomaly["instanceName"])
            if anomaly.get("patternName"):
                patterns.add(anomaly["patternName"])
            if anomaly.get("projectDisplayName"):
                projects.add(anomaly["projectDisplayName"])
            
            # Extract from root cause
            root_cause = anomaly.get("rootCause", {})
            if root_cause.get("metricType"):
                metric_types.add(root_cause["metricType"])
            
            if anomaly.get("zoneName"):
                zones.add(anomaly["zoneName"])
        
        # Calculate time span
        timestamps = [anomaly.get("timestamp", 0) for anomaly in anomalies if anomaly.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        # Top affected patterns (simplified)
        pattern_counts = {}
        for anomaly in anomalies:
            pattern = anomaly.get("patternName", "Unknown")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        
        top_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        return {
            "status": "success",
            "summary": {
                "total_anomalies": total_anomalies,
                "unique_components": len(components),
                "unique_instances": len(instances),
                "unique_patterns": len(patterns),
                "unique_projects": len(projects),
                "unique_metric_types": len(metric_types),
                "unique_zones": len(zones),
                "time_span_hours": time_span_hours,
                "top_patterns": [{"pattern": p, "count": c} for p, c in top_patterns],
                "time_range": {
                    "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                    "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomalies_overview: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomalies overview: {str(e)}"
        }

# ============================================================================
# LAYER 1: COMPACT LIST
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_list(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20,
    sort_by: str = "timestamp",
    include_raw_data: bool = False,
    include_analysis: bool = True,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Enhanced metric anomaly list with comprehensive information.
    This is the main tool for getting metric anomaly details - combines basic info with detailed data.

    ⚠️ CRITICAL FOR LLMs/AI ASSISTANTS: When displaying metric anomalies to users, you MUST include
    the project_name field for EVERY SINGLE anomaly in your output. Do not omit this field because project name and instance information are crucial for understanding the context of each anomaly.

    REQUIRED DISPLAY FORMAT FOR EACH ANOMALY:
        "1. [Anomaly Title]
         • Project: [anomaly.project_name]     ← MANDATORY - ALWAYS DISPLAY THIS LINE
         • Time: [anomaly.datetime]
         • Metric: [anomaly.metric.name]
         • Component: [anomaly.location.component]
         • Instance: [anomaly.location.instance]"
    
    Args:
        system_name: Name of the system to query
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit: Maximum number of anomalies to return
        sort_by: Sort field ("timestamp", "pattern")
        include_raw_data: Whether to include raw anomaly data (default: False for performance)
        include_analysis: Whether to include anomaly analysis (default: True)
        project_name: Optional project name to filter results (if not provided, returns all projects)
        
    Returns:
        Dict containing enhanced list of anomalies with status and metadata.
        
        CRITICAL: Each anomaly contains project_name in TWO places:
        - anomaly["project_name"] ← USE THIS FOR DISPLAY
        - anomaly["project"]
        
        When presenting to users, ALWAYS extract and display: anomaly["project_name"]
    
    Note: Project name is MANDATORY in the output. It appears in both the top-level "project" field
    and within the "location" object for each anomaly.
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
        
        if settings.ENABLE_DEBUG_MESSAGES:
            logger.debug("Using time range: %s to %s", start_time_ms, end_time_ms)
            logger.debug("Filters: limit=%s, sort_by=%s, project_name=%s", limit, sort_by, project_name)
        
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        
        if raw_data.get("status") != "success":
            return raw_data

        if not raw_data.get("data"):
            return {
                "status": "success",
                "message": "No metric anomalies found in the specified time range",
                "total_found": 0,
                "returned_count": 0,
                "anomalies": []
            }
        
        anomalies = raw_data["data"]

        # Print anomalies in formatted json for debugging
        # logger.debug("Anomalies found: %sjson.dumps(anomalies, indent=2)", json.dumps(anomalies, indent=2))

        # Filter by project name if specified
        if project_name:
            # anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName") == project_name]
            anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName", "").lower() == project_name.lower() or anomaly.get("projectDisplayName", "").lower() == project_name.lower()]
        
        filtered_anomalies = anomalies  # No filtering for now, but can be added back if needed
        
        # Sort anomalies
        if sort_by == "pattern":
            filtered_anomalies.sort(key=lambda x: x.get("patternName", ""))
        else:  # timestamp
            filtered_anomalies.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit results
        limited_anomalies = filtered_anomalies[:limit]
        
        # Create enhanced representation
        enhanced_anomalies = []
        for anomaly in limited_anomalies:
            root_cause = anomaly.get("rootCause", {})
            result_info = anomaly.get("rootCauseResultInfo", {})
                        
            # Calculate duration
            time_pairs = root_cause.get("timePairList", [])
            duration_minutes = _calculate_duration_minutes(time_pairs)
            
            # Get project name - prefer projectDisplayName, fallback to projectName
            # This is CRITICAL information that must always be included
            project_name_value = anomaly.get("projectDisplayName") or anomaly.get("projectName") or "Unknown"
            
            enhanced_anomaly = {
                # Top-level project identification - ALWAYS include this
                "project_name": project_name_value,
                "project": project_name_value,
                
                # Timing information
                "timestamp": anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(anomaly.get("timestamp", 0), tz_name) if anomaly.get("timestamp") else None,
                
                "active": anomaly.get("active", 0),
                
                # Location information (includes project again for nested access)
                "location": {
                    "component": anomaly.get("componentName"),
                    "instance": anomaly.get("instanceName"),
                    "zone": anomaly.get("zoneName")
                },
                
                # Metric information (includes project for context)
                "metric": {
                    "name": root_cause.get("metricName"),
                    "type": root_cause.get("metricType", "Unknown"),
                    "pattern_name": anomaly.get("patternName"),
                    "pattern_id": root_cause.get("patternId")
                },
                
                # Anomaly details
                "anomaly_details": {
                    "anomaly_value": root_cause.get("anomalyValue"),
                    "percentage": root_cause.get("percentage"),
                    "sign": root_cause.get("sign"),
                    "is_flapping": root_cause.get("isFlapping", False),
                    "duration_minutes": duration_minutes,
                    "is_alert": root_cause.get("isAlert", False),
                    "is_incident": anomaly.get("isIncident", False)
                },
                
                # System status
                "system_status": {
                    "process_crash": root_cause.get("processCrash", False),
                    "instance_down": root_cause.get("instanceDown", False)
                }
            }
            
            # Add raw data if requested and available
            if include_raw_data:
                enhanced_anomaly["raw_data"] = anomaly
                enhanced_anomaly["raw_data_length"] = len(json.dumps(anomaly))
            elif anomaly:
                # Always include a preview of key raw data fields
                enhanced_anomaly["raw_data_preview"] = {
                    "has_root_cause": bool(root_cause),
                    "has_result_info": bool(result_info),
                    "time_pairs_count": len(time_pairs),
                    "has_full_raw_data": True
                }
            
            enhanced_anomalies.append(enhanced_anomaly)
        
        return {
            "status": "success",
            "total_found": len(filtered_anomalies),
            "returned_count": len(enhanced_anomalies),
            "filters": {
                "sort_by": sort_by,
                "limit": limit,
                "include_raw_data": include_raw_data,
                "include_analysis": include_analysis
            },
            "anomalies": enhanced_anomalies
        }

        #print rdata in formatted json for debugging
        # print(json.dumps(rdata, indent=2))

        # return rdata
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomalies_list: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomalies list: {str(e)}"
        }

# ============================================================================
# LAYER 2: STATISTICS AND ANALYSIS
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_statistics(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    include_trends: bool = True,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Layer 5: Comprehensive statistics for metric anomalies.
    
    Provides statistical analysis, trends, and insights across all anomalies
    in the time range. Good for understanding patterns and overall system health.
    
    Args:
        system_name: Name of the system to query
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        include_trends: Whether to include trend analysis
        project_name: Optional project name to filter results (if not provided, returns all projects)
        
    Returns:
        Dict containing comprehensive statistics with status and metadata
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
        
        if settings.ENABLE_DEBUG_MESSAGES:
            logger.debug("Using time range: %s to %s", start_time_ms, end_time_ms)
            logger.debug("Include trends: %s", include_trends)
        
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        
        if raw_data.get("status") != "success":
            return raw_data

        if not raw_data.get("data"):
            return {
                "status": "success",
                "message": "No metric anomalies found in the specified time range",
                "statistics": {
                    "total_anomalies": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        anomalies = raw_data["data"]
        
        # Filter by project name if specified
        if project_name:
            # anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName") == project_name]
            anomalies = [anomaly for anomaly in anomalies if anomaly.get("projectName", "").lower() == project_name.lower() or anomaly.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Basic statistics
        total_anomalies = len(anomalies)
        
        
        # Component analysis
        component_counts = {}
        instance_counts = {}
        pattern_counts = {}
        project_counts = {}
        metric_type_counts = {}
        zone_counts = {}
        metric_name_counts = {}
        
        # Flags analysis
        flapping_count = 0
        alert_count = 0
        incident_count = 0
        process_crash_count = 0
        instance_down_count = 0
        
        # Sign analysis
        sign_counts = {"higher": 0, "lower": 0, "unknown": 0}
        
        for anomaly in anomalies:
            root_cause = anomaly.get("rootCause", {})
            
            # Component tracking
            component = anomaly.get("componentName", "Unknown")
            component_counts[component] = component_counts.get(component, 0) + 1
            
            instance = anomaly.get("instanceName", "Unknown")
            instance_counts[instance] = instance_counts.get(instance, 0) + 1
            
            pattern = anomaly.get("patternName", "Unknown")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            
            project = anomaly.get("projectDisplayName", "Unknown")
            project_counts[project] = project_counts.get(project, 0) + 1
            
            zone = anomaly.get("zoneName", "Unknown")
            zone_counts[zone] = zone_counts.get(zone, 0) + 1
            
            # Metric analysis
            metric_type = root_cause.get("metricType", "Unknown")
            metric_type_counts[metric_type] = metric_type_counts.get(metric_type, 0) + 1
            
            metric_name = root_cause.get("metricName", "Unknown")
            metric_name_counts[metric_name] = metric_name_counts.get(metric_name, 0) + 1
            
            # Flags
            if root_cause.get("isFlapping"):
                flapping_count += 1
            if root_cause.get("isAlert"):
                alert_count += 1
            if anomaly.get("isIncident"):
                incident_count += 1
            if root_cause.get("processCrash"):
                process_crash_count += 1
            if root_cause.get("instanceDown"):
                instance_down_count += 1
            
            # Sign analysis
            sign = root_cause.get("sign", "unknown")
            if sign in sign_counts:
                sign_counts[sign] += 1
            else:
                sign_counts["unknown"] += 1
        
        # Calculate percentages and top items
        def get_top_items(counts_dict, top_n=5):
            sorted_items = sorted(counts_dict.items(), key=lambda x: x[1], reverse=True)
            return {
                item: {"count": count, "percentage": round(count / total_anomalies * 100, 1)}
                for item, count in sorted_items[:top_n]
            }
        
        # Statistical calculations
        
        # Time analysis
        timestamps = [anomaly.get("timestamp", 0) for anomaly in anomalies if anomaly.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        statistics = {
            "total_anomalies": total_anomalies,
            "time_range": {
                "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1),
                "actual_span_hours": time_span_hours
            },
            
            
            "infrastructure_analysis": {
                "unique_components": len(component_counts),
                "unique_instances": len(instance_counts),
                "unique_projects": len(project_counts),
                "unique_zones": len(zone_counts),
                "top_affected_components": get_top_items(component_counts),
                "top_affected_instances": get_top_items(instance_counts),
                "top_affected_projects": get_top_items(project_counts),
                "zone_distribution": get_top_items(zone_counts)
            },
            
            "metric_analysis": {
                "unique_patterns": len(pattern_counts),
                "unique_metric_types": len(metric_type_counts),
                "unique_metric_names": len(metric_name_counts),
                "top_patterns": get_top_items(pattern_counts),
                "top_metric_types": get_top_items(metric_type_counts),
                "top_metric_names": get_top_items(metric_name_counts),
                "sign_distribution": sign_counts
            },
            
            "behavioral_flags": {
                "flapping_anomalies": {"count": flapping_count, "percentage": round(flapping_count / total_anomalies * 100, 1)},
                "alert_anomalies": {"count": alert_count, "percentage": round(alert_count / total_anomalies * 100, 1)},
                "incident_anomalies": {"count": incident_count, "percentage": round(incident_count / total_anomalies * 100, 1)},
                "process_crash_anomalies": {"count": process_crash_count, "percentage": round(process_crash_count / total_anomalies * 100, 1)},
                "instance_down_anomalies": {"count": instance_down_count, "percentage": round(instance_down_count / total_anomalies * 100, 1)}
            }
        }
        

        return {
            "status": "success",
            "statistics": statistics
        }
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomalies_statistics: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomalies statistics: {str(e)}"
        }

# ============================================================================
# SIMPLE WRAPPER FUNCTION (matches pattern of other tools)
# ============================================================================

@mcp_server.tool()
async def fetch_metric_anomalies(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches metric anomaly timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for metric anomalies, performance issues, or infrastructure monitoring data.
    
    IMPORTANT: Each returned anomaly includes projectName and projectDisplayName fields which identify 
    the project the anomaly belongs to. These fields are CRITICAL for distinguishing anomalies when 
    a system contains multiple projects.

    Args:
        system_name (str): The name of the system to query for metric anomalies.
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        project_name (str): Optional. Project name to filter results (if not provided, returns all projects).
        
    Returns:
        Dict with status and data. Each anomaly in data array includes:
        - projectName: The internal project name
        - projectDisplayName: The user-facing project display name (use this for presentation)
        - All other anomaly fields (timestamp, metrics, etc.)
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

        if settings.ENABLE_DEBUG_MESSAGES:
            logger.debug("Using time range: %s to %s", start_time_ms, end_time_ms)

        # Call the InsightFinder API client with the timeline endpoint
        api_client = _get_api_client()
        result = await api_client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )


        if isinstance(result, dict):
            if result.get("data"):
                # Filter by project name if specified
                if project_name:
                    # filtered_data = [anomaly for anomaly in result["data"] if anomaly.get("projectName") == project_name]
                    filtered_data = [anomaly for anomaly in result["data"] if anomaly.get("projectName", "").lower() == project_name.lower() or anomaly.get("projectDisplayName", "").lower() == project_name.lower()]
                    result["data"] = filtered_data
                # Data found, return as-is
                pass
            
        return result

    except Exception as e:
        error_message = f"Error in fetch_metric_anomalies: {str(e)}"
        return {"status": "error", "message": error_message}


# ============================================================================
# PROJECT-SPECIFIC FUNCTION
# ============================================================================

@mcp_server.tool()
async def get_project_metric_anomalies(
    system_name: str,
    project_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Fetches metric anomalies specifically for a given project within a system.
    Use this tool when the user specifies both a system name and project name.
    
    IMPORTANT: Each anomaly returned MUST include the project_name field. This is essential
    for identifying which project the anomaly belongs to.
    
    Example usage:
    - "show me metric anomalies for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get metric anomalies before incident for project X in system Y"

    Args:
        system_name (str): The name of the system (e.g., "InsightFinder Demo System (APP)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit (int): Maximum number of anomalies to return (default: 20)
        
    Returns:
        Dict with status, summary, and anomalies list. Each anomaly includes:
        - project_name: The project name (ALWAYS included - derived from projectDisplayName or projectName)
        - All other anomaly details (metrics, timestamps, location info, etc.)
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

        # Call the InsightFinder API client with ONLY the system name
        api_client = _get_api_client()
        result = await api_client.get_metricanomaly(
            system_name=system_name,  # Use only the system name here
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        metric_anomalies = result["data"]
        
        # Filter by the specific project name
        # project_anomalies = [ma for ma in metric_anomalies if ma.get("projectName") == project_name]
        project_anomalies = [ma for ma in metric_anomalies if ma.get("projectName", "").lower() == project_name.lower() or ma.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Sort by timestamp (most recent first) and limit
        project_anomalies = sorted(project_anomalies, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]

        # Create detailed anomaly list for the project
        anomaly_list = []
        for i, anomaly in enumerate(project_anomalies):
            root_cause = anomaly.get("rootCause", {})
            
            # Calculate duration
            time_pairs = root_cause.get("timePairList", [])
            duration_minutes = _calculate_duration_minutes(time_pairs)
            
            # Get project name - prefer projectDisplayName, fallback to projectName
            project_display_name = anomaly.get("projectDisplayName") or anomaly.get("projectName") or project_name
            
            anomaly_summary = {
                "index": i + 1,
                "timestamp": anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(anomaly.get("timestamp", 0), tz_name) if anomaly.get("timestamp") else None,
                "active": anomaly.get("active", 0),
                
                # Location information
                "project_name": project_display_name,
                "component_name": anomaly.get("componentName"),
                "instance_name": anomaly.get("instanceName"),
                "zone_name": anomaly.get("zoneName"),
                
                # Metric information
                "pattern_name": anomaly.get("patternName"),
                "metric_name": root_cause.get("metricName"),
                "metric_type": root_cause.get("metricType", "Unknown"),
                
                # Anomaly details
                "anomaly_value": root_cause.get("anomalyValue"),
                "percentage": root_cause.get("percentage"),
                "sign": root_cause.get("sign"),
                "duration_minutes": duration_minutes,
                "is_flapping": root_cause.get("isFlapping", False),
                "is_alert": root_cause.get("isAlert", False),
                "is_incident": anomaly.get("isIncident", False),
                
                # System status flags
                "process_crash": root_cause.get("processCrash", False),
                "instance_down": root_cause.get("instanceDown", False)
            }
            
            anomaly_list.append(anomaly_summary)

        # Summary statistics
        total_anomalies = len(project_anomalies)
        active_count = 0
        for anomaly in project_anomalies:
            if anomaly.get("active", 0) == 1:
                active_count += 1

        return {
            "status": "success",
            "message": f"Found {total_anomalies} metric anomalies for project '{project_name}' in system '{system_name}'",
            "summary": {
                "total_anomalies": total_anomalies,
                "active_anomalies": active_count,
                "project_name": project_name,
                "system_name": system_name,
                "timezone": tz_name,
                "time_range": {
                    "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                    "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            },
            "anomalies": anomaly_list
        }

    except Exception as e:
        error_message = f"Error in get_project_metric_anomalies: {str(e)}"
        logger.error(error_message)
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


def _calculate_duration_minutes(time_pairs: List[Dict[str, int]]) -> float:
    """Calculate total duration in minutes from time pairs."""
    if not time_pairs:
        return 0.0
    
    total_duration_ms = 0
    for pair in time_pairs:
        start = pair.get("s", 0)
        end = pair.get("e", 0)
        if end > start:
            total_duration_ms += (end - start)
    
    return round(total_duration_ms / (1000 * 60), 2)