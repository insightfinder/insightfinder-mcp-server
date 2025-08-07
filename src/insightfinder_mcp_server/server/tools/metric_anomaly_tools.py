"""
Multi-layered metric anomaly tools for the InsightFinder MCP server.

This module provides a progressive drill-down approach for exploring metric anomalies:
- Layer 0: Ultra-compact overview (get_metric_anomalies_overview)
- Layer 1: Compact list (get_metric_anomalies_list) 
- Layer 2: Detailed summary (get_metric_anomalies_summary)
- Layer 3: Full details (get_metric_anomaly_details)
- Layer 4: Raw data (get_metric_anomaly_raw_data)
- Layer 5: Statistics (get_metric_anomalies_statistics)

Each layer provides increasingly detailed information while maintaining LLM-friendly,
structured outputs optimized for analysis and reasoning.
"""

import asyncio
import json
import logging
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..server import mcp_server
from ...api_client.insightfinder_client import api_client
from .get_time import get_timezone_aware_time_range_ms, format_timestamp_in_user_timezone, format_api_timestamp_corrected, get_today_time_range_ms

logger = logging.getLogger(__name__)

# ============================================================================
# LAYER 0: ULTRA-COMPACT OVERVIEW
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_overview(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None
) -> Dict[str, Any]:
    """
    Layer 0: Ultra-compact overview of metric anomalies.
    
    Provides the most condensed view possible - just essential counts and high-level patterns.
    Perfect for initial assessment and determining if deeper investigation is needed.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds (optional, defaults to 24 hours ago)
        end_time_ms: End timestamp in milliseconds (optional, defaults to current time)
        
    Returns:
        Dict containing ultra-compact overview with status, summary stats, and key insights
    """
    try:
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(24)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        client = api_client
        
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
        
        if not anomalies:
            return {
                "status": "success",
                "message": "No metric anomalies found in the specified time range",
                "summary": {
                    "total_anomalies": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms),
                        "end": format_timestamp_in_user_timezone(end_time_ms),
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
        metric_types = set()
        zones = set()
        
        for anomaly in anomalies:
            if anomaly.get("componentName"):
                components.add(anomaly["componentName"])
            if anomaly.get("instanceName"):
                instances.add(anomaly["instanceName"])
            if anomaly.get("patternName"):
                patterns.add(anomaly["patternName"])
            
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
                "unique_metric_types": len(metric_types),
                "unique_zones": len(zones),
                "time_span_hours": time_span_hours,
                "top_patterns": [{"pattern": p, "count": c} for p, c in top_patterns],
                "time_range": {
                    "start": format_timestamp_in_user_timezone(start_time_ms),
                    "end": format_timestamp_in_user_timezone(end_time_ms),
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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 20,
    min_severity: str = "low",
    sort_by: str = "timestamp"
) -> Dict[str, Any]:
    """
    Layer 1: Compact list of metric anomalies.
    
    Provides a condensed list with essential details for each anomaly.
    Good for scanning and identifying anomalies of interest.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds (optional, defaults to 24 hours ago)
        end_time_ms: End timestamp in milliseconds (optional, defaults to current time)
        limit: Maximum number of anomalies to return
        min_severity: Minimum severity level ("low", "medium", "high", "critical")
        sort_by: Sort field ("timestamp", "severity", "pattern")
        
    Returns:
        Dict containing compact list of anomalies with status and metadata
    """
    try:
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(24)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        client = api_client
        
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
        
        # Create compact representation
        compact_anomalies = []
        for anomaly in limited_anomalies:
            root_cause = anomaly.get("rootCause", {})
            
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
            
            compact_anomaly = {
                "timestamp": anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(anomaly.get("timestamp", 0)) if anomaly.get("timestamp") else None,
                "severity": severity,
                "anomaly_score": score,
                "component": anomaly.get("componentName"),
                "instance": anomaly.get("instanceName"),
                "pattern": anomaly.get("patternName"),
                "metric_name": root_cause.get("metricName"),
                "metric_type": root_cause.get("metricType", "Unknown"),
                "zone": anomaly.get("zoneName"),
                "anomaly_value": root_cause.get("anomalyValue"),
                "percentage": root_cause.get("percentage"),
                "sign": root_cause.get("sign"),
                "is_flapping": root_cause.get("isFlapping", False),
                "duration_minutes": _calculate_duration_minutes(root_cause.get("timePairList", []))
            }
            
            compact_anomalies.append(compact_anomaly)
        
        return {
            "status": "success",
            "total_found": len(filtered_anomalies),
            "returned_count": len(compact_anomalies),
            "filters": {
                "min_severity": min_severity,
                "sort_by": sort_by,
                "limit": limit
            },
            "anomalies": compact_anomalies
        }
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomalies_list: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomalies list: {str(e)}"
        }

# ============================================================================
# LAYER 2: DETAILED SUMMARY
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_summary(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 10,
    min_severity: str = "medium",
    include_context: bool = True
) -> Dict[str, Any]:
    """
    Layer 2: Detailed summary of metric anomalies.
    
    Provides rich details for each anomaly including context, patterns, and analysis.
    Good for understanding the nature and impact of each anomaly.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds (optional, defaults to 24 hours ago)
        end_time_ms: End timestamp in milliseconds (optional, defaults to current time)
        limit: Maximum number of anomalies to return
        min_severity: Minimum severity level ("low", "medium", "high", "critical")
        include_context: Whether to include contextual analysis
        
    Returns:
        Dict containing detailed anomaly summaries with status and metadata
    """
    try:
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(24)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        client = api_client
        
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
        
        # Convert severity level to score threshold
        severity_thresholds = {
            "low": 0,
            "medium": 1,
            "high": 5,
            "critical": 10
        }
        min_score = severity_thresholds.get(min_severity, 1)
        
        # Filter by severity and sort by score (highest first)
        filtered_anomalies = [
            anomaly for anomaly in anomalies 
            if anomaly.get("anomalyScore", 0) >= min_score
        ]
        filtered_anomalies.sort(key=lambda x: x.get("anomalyScore", 0), reverse=True)
        
        # Limit results
        limited_anomalies = filtered_anomalies[:limit]
        
        # Create detailed summaries
        detailed_anomalies = []
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
            
            # Calculate duration and time pairs
            time_pairs = root_cause.get("timePairList", [])
            duration_minutes = _calculate_duration_minutes(time_pairs)
            
            detailed_anomaly = {
                "timestamp": anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(anomaly.get("timestamp", 0)) if anomaly.get("timestamp") else None,
                "severity": severity,
                "anomaly_score": score,
                "active": anomaly.get("active", 0),
                
                # Location information
                "location": {
                    "component": anomaly.get("componentName"),
                    "instance": anomaly.get("instanceName"),
                    "zone": anomaly.get("zoneName")
                },
                
                # Metric information
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
                    "slop": root_cause.get("slop"),
                    "is_alert": root_cause.get("isAlert", False),
                    "ignore_flag": root_cause.get("ignoreFlag", False)
                },
                
                # Timing information
                "timing": {
                    "duration_minutes": duration_minutes,
                    "time_pairs": time_pairs,
                    "earliest_creation_timestamp": root_cause.get("earliestCreationTimestamp", 0),
                    "real_value_at_creation": root_cause.get("realValueAtCreationTimestamp", 0.0)
                },
                
                # System status
                "system_status": {
                    "process_crash": root_cause.get("processCrash", False),
                    "instance_down": root_cause.get("instanceDown", False),
                    "is_incident": anomaly.get("isIncident", False)
                },
                
                # Contextual information
                "context": {
                    "has_preceding_event": result_info.get("hasPrecedingEvent", False),
                    "has_trailing_event": result_info.get("hasTrailingEvent", False),
                    "caused_by_change_event": result_info.get("causedByChangeEvent", False),
                    "lead_to_incident": result_info.get("leadToIncident", False)
                }
            }
            
            # Add analysis if requested
            if include_context:
                detailed_anomaly["analysis"] = _analyze_metric_anomaly(anomaly)
            
            detailed_anomalies.append(detailed_anomaly)
        
        return {
            "status": "success",
            "total_found": len(filtered_anomalies),
            "returned_count": len(detailed_anomalies),
            "filters": {
                "min_severity": min_severity,
                "limit": limit,
                "include_context": include_context
            },
            "anomalies": detailed_anomalies,
            "summary_stats": _calculate_summary_stats(detailed_anomalies) if include_context else None
        }
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomalies_summary: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomalies summary: {str(e)}"
        }

# ============================================================================
# LAYER 3: FULL DETAILS
# ============================================================================

@mcp_server.tool()
async def get_metric_anomaly_details(
    system_name: str,
    anomaly_timestamp: int,
    include_analysis: bool = True
) -> Dict[str, Any]:
    """
    Layer 3: Full details for a specific metric anomaly.
    
    Provides complete information about a single anomaly including all available
    metadata, analysis, and contextual information.
    
    Args:
        system_name: Name of the system to query
        anomaly_timestamp: Timestamp of the specific anomaly
        include_analysis: Whether to include detailed analysis
        
    Returns:
        Dict containing complete anomaly details with status and metadata
    """
    try:
        client = api_client
        
        # Use a small time window around the timestamp to find the specific anomaly
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = anomaly_timestamp - window_ms
        end_time_ms = anomaly_timestamp + window_ms
        
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
                "status": "error",
                "message": f"No metric anomaly found at timestamp {anomaly_timestamp}"
            }
        
        # Find the specific anomaly
        target_anomaly = None
        for anomaly in raw_data["data"]:
            if anomaly.get("timestamp") == anomaly_timestamp:
                target_anomaly = anomaly
                break
        
        if not target_anomaly:
            return {
                "status": "error",
                "message": f"Specific metric anomaly not found at timestamp {anomaly_timestamp}"
            }
        
        # Extract all available information
        root_cause = target_anomaly.get("rootCause", {})
        result_info = target_anomaly.get("rootCauseResultInfo", {})
        
        # Determine severity level
        score = target_anomaly.get("anomalyScore", 0)
        if score >= 10:
            severity = "critical"
        elif score >= 5:
            severity = "high"
        elif score >= 1:
            severity = "medium"
        else:
            severity = "low"
        
        # Calculate duration and time pairs
        time_pairs = root_cause.get("timePairList", [])
        duration_minutes = _calculate_duration_minutes(time_pairs)
        
        detailed_info = {
            "status": "success",
            "anomaly": {
                # Basic identification
                "timestamp": target_anomaly.get("timestamp"),
                "datetime": format_api_timestamp_corrected(target_anomaly.get("timestamp", 0)) if target_anomaly.get("timestamp") else None,
                "severity": severity,
                "anomaly_score": score,
                "active": target_anomaly.get("active", 0),
                
                # Location and infrastructure
                "infrastructure": {
                    "component_name": target_anomaly.get("componentName"),
                    "instance_name": target_anomaly.get("instanceName"),
                    "zone_name": target_anomaly.get("zoneName")
                },
                
                # Metric details
                "metric_details": {
                    "metric_name": root_cause.get("metricName"),
                    "metric_type": root_cause.get("metricType", "Unknown"),
                    "pattern_name": target_anomaly.get("patternName"),
                    "pattern_id": root_cause.get("patternId"),
                    "anomaly_value": root_cause.get("anomalyValue"),
                    "percentage": root_cause.get("percentage"),
                    "sign": root_cause.get("sign"),
                    "slop": root_cause.get("slop")
                },
                
                # Temporal information
                "temporal_info": {
                    "duration_minutes": duration_minutes,
                    "time_pairs": time_pairs,
                    "is_flapping": root_cause.get("isFlapping", False),
                    "earliest_creation_timestamp": root_cause.get("earliestCreationTimestamp", 0),
                    "real_value_at_creation": root_cause.get("realValueAtCreationTimestamp", 0.0)
                },
                
                # System state
                "system_state": {
                    "process_crash": root_cause.get("processCrash", False),
                    "instance_down": root_cause.get("instanceDown", False),
                    "is_incident": target_anomaly.get("isIncident", False),
                    "is_alert": root_cause.get("isAlert", False),
                    "ignore_flag": root_cause.get("ignoreFlag", False)
                },
                
                # Root cause context
                "root_cause_context": {
                    "has_preceding_event": result_info.get("hasPrecedingEvent", False),
                    "has_trailing_event": result_info.get("hasTrailingEvent", False),
                    "caused_by_change_event": result_info.get("causedByChangeEvent", False),
                    "lead_to_incident": result_info.get("leadToIncident", False)
                }
            }
        }
        
        # Add comprehensive analysis if requested
        if include_analysis:
            detailed_info["analysis"] = _comprehensive_anomaly_analysis(target_anomaly)
        
        # Add raw data availability info
        detailed_info["has_raw_data"] = True  # Metric anomalies typically have the full data
        
        return detailed_info
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomaly_details: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomaly details: {str(e)}"
        }

# ============================================================================
# LAYER 4: RAW DATA
# ============================================================================

@mcp_server.tool()
async def get_metric_anomaly_raw_data(
    system_name: str,
    anomaly_timestamp: int,
    max_length: int = 10000
) -> Dict[str, Any]:
    """
    Layer 4: Raw data for a specific metric anomaly.
    
    Provides the complete raw JSON data for detailed analysis and debugging.
    Useful for advanced analysis, integration with other tools, or troubleshooting.
    
    Args:
        system_name: Name of the system to query
        anomaly_timestamp: Timestamp of the specific anomaly
        max_length: Maximum length of raw data to return (for truncation)
        
    Returns:
        Dict containing raw anomaly data with status and metadata
    """
    try:
        client = api_client
        
        # Use a small time window around the timestamp to find the specific anomaly
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = anomaly_timestamp - window_ms
        end_time_ms = anomaly_timestamp + window_ms
        
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
                "status": "error",
                "message": f"No metric anomaly found at timestamp {anomaly_timestamp}"
            }
        
        # Find the specific anomaly
        target_anomaly = None
        for anomaly in raw_data["data"]:
            if anomaly.get("timestamp") == anomaly_timestamp:
                target_anomaly = anomaly
                break
        
        if not target_anomaly:
            return {
                "status": "error",
                "message": f"Specific metric anomaly not found at timestamp {anomaly_timestamp}"
            }
        # Convert to JSON string and check length
        raw_json = json.dumps(target_anomaly, indent=2)
        original_length = len(raw_json)
        truncated = False
        
        if original_length > max_length:
            raw_json = raw_json[:max_length] + "\n... [TRUNCATED]"
            truncated = True
        
        return {
            "status": "success",
            "anomaly_timestamp": anomaly_timestamp,
            "raw_data": raw_json,
            "raw_data_length": original_length,
            "returned_length": len(raw_json),
            "truncated": truncated,
            "metadata": {
                "data_structure": "InsightFinder metric anomaly timeline entry",
                "contains": [
                    "timestamp and identification",
                    "rootCause with metric details",
                    "anomaly score and severity",
                    "time pairs and duration",
                    "system state information",
                    "root cause result info"
                ],
                "useful_for": [
                    "detailed metric analysis",
                    "integration with external tools",
                    "debugging and troubleshooting",
                    "custom analytics and reporting"
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_metric_anomaly_raw_data: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get metric anomaly raw data: {str(e)}"
        }

# ============================================================================
# LAYER 5: STATISTICS
# ============================================================================

@mcp_server.tool()
async def get_metric_anomalies_statistics(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    include_trends: bool = True
) -> Dict[str, Any]:
    """
    Layer 5: Comprehensive statistics for metric anomalies.
    
    Provides statistical analysis, trends, and insights across all anomalies
    in the time range. Good for understanding patterns and overall system health.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds (optional, defaults to 24 hours ago)
        end_time_ms: End timestamp in milliseconds (optional, defaults to current time)
        include_trends: Whether to include trend analysis
        
    Returns:
        Dict containing comprehensive statistics with status and metadata
    """
    try:
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(24)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        client = api_client
        
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
                        "start": format_timestamp_in_user_timezone(start_time_ms),
                        "end": format_timestamp_in_user_timezone(end_time_ms),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        anomalies = raw_data["data"]
        
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
                "start": format_timestamp_in_user_timezone(start_time_ms),
                "end": format_timestamp_in_user_timezone(end_time_ms),
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
                "unique_zones": len(zone_counts),
                "top_affected_components": get_top_items(component_counts),
                "top_affected_instances": get_top_items(instance_counts),
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
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches metric anomaly timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for metric anomalies, performance issues, or infrastructure monitoring data.

    Args:
        system_name (str): The name of the system to query for metric anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 24 hours ago.
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
    """
    try:
        
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(24)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms

        # Call the InsightFinder API client with the timeline endpoint
        result = await api_client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if isinstance(result, dict):
            if result.get("data"):
                # Data found, return as-is
                pass
            
        return result

    except Exception as e:
        error_message = f"Error in fetch_metric_anomalies: {str(e)}"
        return {"status": "error", "message": error_message}

# Add today-specific metric anomaly tool
@mcp_server.tool()
async def get_today_metric_anomalies(
    system_name: str,
    min_severity: str = "low"
) -> Dict[str, Any]:
    """
    Fetches metric anomalies for today in the user's timezone.
    Use this tool when a user asks for "today's metric anomalies", "metric anomalies today", etc.

    Args:
        system_name (str): The name of the system to query for metric anomalies.
        min_severity (str): Minimum severity level ("low", "medium", "high", "critical").
    """
    try:
        
        # Get today's time range in user's timezone
        start_time_ms, end_time_ms = get_today_time_range_ms()
        

        # Call the InsightFinder API client
        result = await api_client.get_metricanomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result.get("status") != "success":
            return result

        anomalies = result.get("data", [])
        
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
        
        # Sort by timestamp (most recent first)
        filtered_anomalies = sorted(filtered_anomalies, key=lambda x: x.get("timestamp", 0), reverse=True)

        # Create anomaly list with timezone-aware timestamps
        anomaly_list = []
        for i, anomaly in enumerate(filtered_anomalies):
            root_cause = anomaly.get("rootCause", {})
            
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
            
            anomaly_info = {
                "id": i + 1,
                "timestamp": anomaly.get("timestamp"),
                "timestamp_human": format_api_timestamp_corrected(anomaly.get("timestamp", 0)),
                "severity": severity,
                "anomaly_score": score,
                "component": anomaly.get("componentName"),
                "instance": anomaly.get("instanceName"),
                "pattern": anomaly.get("patternName"),
                "metric_name": root_cause.get("metricName"),
                "metric_type": root_cause.get("metricType", "Unknown"),
                "zone": anomaly.get("zoneName"),
                "anomaly_value": root_cause.get("anomalyValue"),
                "percentage": root_cause.get("percentage"),
                "sign": root_cause.get("sign"),
                "is_flapping": root_cause.get("isFlapping", False)
            }
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "system_name": system_name,
            "query_type": "today_metric_anomalies",
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms),
                "start_raw_ms": start_time_ms,
                "end_raw_ms": end_time_ms,
                "description": "Today in user's timezone (midnight to current time)"
            },
            "filters": {
                "min_severity": min_severity
            },
            "total_found": len(anomalies),
            "filtered_count": len(anomaly_list),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_today_metric_anomalies: {str(e)}"
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

def _comprehensive_anomaly_analysis(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """Provide comprehensive analysis for a metric anomaly."""
    basic_analysis = _analyze_metric_anomaly(anomaly)
    root_cause = anomaly.get("rootCause", {})
    result_info = anomaly.get("rootCauseResultInfo", {})
    
    # Enhanced analysis
    enhanced_analysis = {
        **basic_analysis,
        "metric_behavior": {},
        "temporal_patterns": {},
        "system_context": {},
        "risk_assessment": {}
    }
    
    # Metric behavior analysis
    percentage = root_cause.get("percentage", 0)
    anomaly_value = root_cause.get("anomalyValue")
    sign = root_cause.get("sign")
    
    enhanced_analysis["metric_behavior"] = {
        "deviation_percentage": percentage,
        "direction": sign,
        "magnitude": "extreme" if percentage > 500 else "high" if percentage > 200 else "moderate" if percentage > 100 else "low",
        "anomaly_value": anomaly_value,
        "pattern_id": root_cause.get("patternId")
    }
    
    # Temporal patterns
    time_pairs = root_cause.get("timePairList", [])
    duration_minutes = _calculate_duration_minutes(time_pairs)
    
    enhanced_analysis["temporal_patterns"] = {
        "duration_minutes": duration_minutes,
        "duration_category": "extended" if duration_minutes > 60 else "sustained" if duration_minutes > 15 else "brief",
        "is_flapping": root_cause.get("isFlapping", False),
        "time_pairs_count": len(time_pairs)
    }
    
    # System context
    enhanced_analysis["system_context"] = {
        "has_related_events": result_info.get("hasPrecedingEvent") or result_info.get("hasTrailingEvent"),
        "part_of_larger_issue": result_info.get("leadToIncident"),
        "change_related": result_info.get("causedByChangeEvent"),
        "infrastructure_impact": root_cause.get("processCrash") or root_cause.get("instanceDown")
    }
    
    # Risk assessment
    score = anomaly.get("anomalyScore", 0)
    risk_level = "critical" if score >= 10 else "high" if score >= 5 else "medium" if score >= 1 else "low"
    
    enhanced_analysis["risk_assessment"] = {
        "risk_level": risk_level,
        "requires_immediate_attention": score >= 5 or root_cause.get("processCrash") or root_cause.get("instanceDown"),
        "potential_for_escalation": result_info.get("hasTrailingEvent") or root_cause.get("isFlapping"),
        "business_impact": "high" if result_info.get("leadToIncident") else "medium" if score >= 5 else "low"
    }
    
    return enhanced_analysis

def _calculate_summary_stats(anomalies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics for a list of anomalies."""
    if not anomalies:
        return {}
    
    total = len(anomalies)
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    
    for anomaly in anomalies:
        severity = anomaly.get("severity", "low")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    
    return {
        "total_anomalies": total,
        "severity_distribution": severity_counts,
        "severity_percentages": {k: round(v / total * 100, 1) for k, v in severity_counts.items()}
    }

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
