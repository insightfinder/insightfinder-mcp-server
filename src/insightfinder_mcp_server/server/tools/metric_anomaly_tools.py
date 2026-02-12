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
        start_time: Start timestamp (optional, defaults to 24 hours ago). Accepts human-readable formats.
        end_time: End timestamp (optional, defaults to current time). Accepts human-readable formats.
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
        
        # Severity analysis based on anomaly score
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for anomaly in anomalies:
            score = anomaly.get("anomalyScore", 0)
            if score >= 10:
                severity_counts["critical"] += 1
            elif score >= 5:
                severity_counts["high"] += 1
            elif score >= 1:
                severity_counts["medium"] += 1
            else:
                severity_counts["low"] += 1
        
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
                "severity_distribution": severity_counts,
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
            },
            "insights": {
                "most_critical_severity": next((k for k, v in severity_counts.items() if v > 0), "none"),
                "pattern_diversity": "high" if len(patterns) > 5 else "low" if len(patterns) <= 2 else "medium",
                "geographic_spread": "multi-zone" if len(zones) > 1 else "single-zone",
                "metric_type_diversity": "high" if len(metric_types) > 3 else "low" if len(metric_types) <= 1 else "medium"
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
    min_severity: str = "low",
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
         • Severity: [anomaly.severity]
         • Component: [anomaly.location.component]
         • Instance: [anomaly.location.instance]"
    
    Args:
        system_name: Name of the system to query
        start_time: Start timestamp (optional, defaults to 24 hours ago). Accepts human-readable formats.
        end_time: End timestamp (optional, defaults to current time). Accepts human-readable formats.
        limit: Maximum number of anomalies to return
        min_severity: Minimum severity level ("low", "medium", "high", "critical")
        sort_by: Sort field ("timestamp", "severity", "pattern")
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
            logger.debug("Filters: limit=%s, min_severity=%s, sort_by=%s, project_name=%s", limit, min_severity, sort_by, project_name)
        
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
        
        # Convert severity level to score threshold
        severity_thresholds = {
            "low": 0,
            "medium": 1,
            "high": 5,
            "critical": 10
        }
        min_score = severity_thresholds.get(min_severity, 0)
        
        # Filter by severity
        filtered_anomalies = [
            anomaly for anomaly in anomalies 
            if anomaly.get("anomalyScore", 0) >= min_score
        ]
        
        # Sort anomalies
        if sort_by == "severity":
            filtered_anomalies.sort(key=lambda x: x.get("anomalyScore", 0), reverse=True)
        elif sort_by == "pattern":
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
            
            # Determine severity level
            score = anomaly.get("anomalyScore", 0)
            if score >= 10:
                severity = "critical"
            elif score >= 5:
                severity = "high"
            elif score >= 1:
                severity = "medium"
            else:
                severity = "low"
            
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
                
                # Severity information
                "severity": severity,
                "anomaly_score": score,
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
            
            # Add analysis if requested
            if include_analysis:
                enhanced_anomaly["analysis"] = _analyze_metric_anomaly(anomaly)
            
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
                "min_severity": min_severity,
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
        start_time: Start timestamp (optional, defaults to 24 hours ago). Accepts human-readable formats.
        end_time: End timestamp (optional, defaults to current time). Accepts human-readable formats.
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
        
        # Severity distribution
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        severity_scores = []
        
        for anomaly in anomalies:
            score = anomaly.get("anomalyScore", 0)
            severity_scores.append(score)
            
            if score >= 10:
                severity_counts["critical"] += 1
            elif score >= 5:
                severity_counts["high"] += 1
            elif score >= 1:
                severity_counts["medium"] += 1
            else:
                severity_counts["low"] += 1
        
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
        avg_score = sum(severity_scores) / len(severity_scores) if severity_scores else 0
        max_score = max(severity_scores) if severity_scores else 0
        min_score = min(severity_scores) if severity_scores else 0
        
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
            
            "severity_analysis": {
                "distribution": severity_counts,
                "percentages": {k: round(v / total_anomalies * 100, 1) for k, v in severity_counts.items()},
                "average_score": round(avg_score, 2),
                "max_score": max_score,
                "min_score": min_score
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
        
        # Add trend analysis if requested
        if include_trends and total_anomalies > 0:
            statistics["trend_analysis"] = _calculate_trends(anomalies, start_time_ms, end_time_ms)
        
        return {
            "status": "success",
            "statistics": statistics,
            "insights": _generate_insights(statistics)
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
        - All other anomaly fields (timestamp, severity, metrics, etc.)
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
        - All other anomaly details (severity, metrics, timestamps, location info, etc.)
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
            
            # Determine severity category
            score = anomaly.get("anomalyScore", 0)
            if score >= 10:
                severity = "critical"
            elif score >= 5:
                severity = "high"
            elif score >= 1:
                severity = "medium"
            else:
                severity = "low"
                
            # Calculate duration
            time_pairs = root_cause.get("timePairList", [])
            duration_minutes = _calculate_duration_minutes(time_pairs)
            
            # Get project name - prefer projectDisplayName, fallback to projectName
            project_display_name = anomaly.get("projectDisplayName") or anomaly.get("projectName") or project_name
            
            anomaly_summary = {
                "index": i + 1,
                "timestamp": anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(anomaly.get("timestamp", 0), tz_name) if anomaly.get("timestamp") else None,
                "severity": severity,
                "anomaly_score": score,
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
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        active_count = 0
        
        for anomaly in project_anomalies:
            score = anomaly.get("anomalyScore", 0)
            if score >= 10:
                severity_counts["critical"] += 1
            elif score >= 5:
                severity_counts["high"] += 1
            elif score >= 1:
                severity_counts["medium"] += 1
            else:
                severity_counts["low"] += 1
                
            if anomaly.get("active", 0) == 1:
                active_count += 1

        return {
            "status": "success",
            "message": f"Found {total_anomalies} metric anomalies for project '{project_name}' in system '{system_name}'",
            "summary": {
                "total_anomalies": total_anomalies,
                "active_anomalies": active_count,
                "severity_distribution": severity_counts,
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

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

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

def _analyze_metric_anomaly(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a single metric anomaly and provide insights."""
    root_cause = anomaly.get("rootCause", {})
    result_info = anomaly.get("rootCauseResultInfo", {})
    
    analysis = {
        "severity_assessment": "low",
        "impact_level": "minimal",
        "urgency": "low",
        "characteristics": [],
        "recommendations": []
    }
    
    # Severity assessment
    score = anomaly.get("anomalyScore", 0)
    if score >= 10:
        analysis["severity_assessment"] = "critical"
        analysis["impact_level"] = "severe"
        analysis["urgency"] = "immediate"
    elif score >= 5:
        analysis["severity_assessment"] = "high"
        analysis["impact_level"] = "significant"
        analysis["urgency"] = "high"
    elif score >= 1:
        analysis["severity_assessment"] = "medium"
        analysis["impact_level"] = "moderate"
        analysis["urgency"] = "medium"
    
    # Characteristic analysis
    if root_cause.get("isFlapping"):
        analysis["characteristics"].append("flapping behavior")
        analysis["recommendations"].append("investigate metric stability")
    
    if root_cause.get("processCrash"):
        analysis["characteristics"].append("process crash detected")
        analysis["recommendations"].append("check process health and logs")
    
    if root_cause.get("instanceDown"):
        analysis["characteristics"].append("instance down")
        analysis["recommendations"].append("verify instance availability")
    
    if result_info.get("causedByChangeEvent"):
        analysis["characteristics"].append("related to change event")
        analysis["recommendations"].append("review recent changes")
    
    if result_info.get("leadToIncident"):
        analysis["characteristics"].append("led to incident")
        analysis["recommendations"].append("assess incident impact")
    
    # Metric type specific insights
    metric_type = root_cause.get("metricType", "Unknown")
    if metric_type == "Storage Utilization":
        analysis["recommendations"].append("monitor disk space and I/O patterns")
    elif metric_type == "Network Utilization":
        analysis["recommendations"].append("check network bandwidth and connectivity")
    elif "CPU" in metric_type or "Processor" in metric_type:
        analysis["recommendations"].append("analyze CPU usage patterns and load")
    elif "Memory" in metric_type:
        analysis["recommendations"].append("review memory allocation and usage")
    
    return analysis

def _calculate_trends(anomalies: List[Dict[str, Any]], start_time_ms: int, end_time_ms: int) -> Dict[str, Any]:
    """Calculate trend analysis for anomalies over time."""
    if not anomalies:
        return {}
    
    # Divide time range into buckets for trend analysis
    time_range_ms = end_time_ms - start_time_ms
    bucket_size_ms = time_range_ms // 6  # 6 buckets for trend analysis
    
    buckets = []
    for i in range(6):
        bucket_start = start_time_ms + (i * bucket_size_ms)
        bucket_end = bucket_start + bucket_size_ms
        buckets.append({
            "start": bucket_start,
            "end": bucket_end,
            "count": 0,
            "total_score": 0
        })
    
    # Distribute anomalies into buckets
    for anomaly in anomalies:
        timestamp = anomaly.get("timestamp", 0)
        score = anomaly.get("anomalyScore", 0)
        
        for bucket in buckets:
            if bucket["start"] <= timestamp < bucket["end"]:
                bucket["count"] += 1
                bucket["total_score"] += score
                break
    
    # Calculate trend metrics
    counts = [bucket["count"] for bucket in buckets]
    avg_scores = [bucket["total_score"] / bucket["count"] if bucket["count"] > 0 else 0 for bucket in buckets]
    
    # Simple trend calculation (positive = increasing, negative = decreasing)
    count_trend = 0
    score_trend = 0
    
    if len(counts) >= 2:
        count_trend = (counts[-1] - counts[0]) / max(counts[0], 1)
        if len([s for s in avg_scores if s > 0]) >= 2:
            non_zero_scores = [s for s in avg_scores if s > 0]
            score_trend = (non_zero_scores[-1] - non_zero_scores[0]) / max(non_zero_scores[0], 0.1)
    
    return {
        "time_buckets": buckets,
        "trend_indicators": {
            "anomaly_frequency_trend": "increasing" if count_trend > 0.2 else "decreasing" if count_trend < -0.2 else "stable",
            "severity_trend": "increasing" if score_trend > 0.2 else "decreasing" if score_trend < -0.2 else "stable",
            "count_trend_value": round(count_trend, 3),
            "score_trend_value": round(score_trend, 3)
        }
    }

def _generate_insights(statistics: Dict[str, Any]) -> List[str]:
    """Generate actionable insights from statistics."""
    insights = []
    
    total = statistics.get("total_anomalies", 0)
    if total == 0:
        return ["No metric anomalies detected in the specified time range"]
    
    severity = statistics.get("severity_analysis", {})
    infrastructure = statistics.get("infrastructure_analysis", {})
    metric_analysis = statistics.get("metric_analysis", {})
    flags = statistics.get("behavioral_flags", {})
    
    # Severity insights
    critical_pct = severity.get("percentages", {}).get("critical", 0)
    high_pct = severity.get("percentages", {}).get("high", 0)
    
    if critical_pct > 20:
        insights.append(f"High concentration of critical anomalies ({critical_pct}%) indicates severe system issues")
    elif critical_pct + high_pct > 50:
        insights.append(f"Majority of anomalies ({critical_pct + high_pct}%) are high severity, requiring immediate attention")
    
    # Infrastructure insights
    unique_components = infrastructure.get("unique_components", 0)
    if unique_components == 1:
        insights.append("Anomalies concentrated in a single component - potential component-specific issue")
    elif unique_components > 10:
        insights.append("Anomalies spread across many components - potential system-wide issue")
    
    # Metric type insights
    top_metric_types = metric_analysis.get("top_metric_types", {})
    if "Storage Utilization" in top_metric_types:
        insights.append("Storage utilization anomalies detected - monitor disk space and I/O")
    if "Network Utilization" in top_metric_types:
        insights.append("Network utilization anomalies detected - check bandwidth and connectivity")
    
    # Behavioral insights
    flapping_pct = flags.get("flapping_anomalies", {}).get("percentage", 0)
    if flapping_pct > 30:
        insights.append(f"High rate of flapping anomalies ({flapping_pct}%) indicates unstable metrics")
    
    incident_pct = flags.get("incident_anomalies", {}).get("percentage", 0)
    if incident_pct > 20:
        insights.append(f"Significant portion of anomalies ({incident_pct}%) led to incidents")
    
    return insights

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
