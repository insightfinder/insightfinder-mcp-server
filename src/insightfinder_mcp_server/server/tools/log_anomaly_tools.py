import sys
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import get_timezone_aware_time_range_ms, format_timestamp_in_user_timezone, format_api_timestamp_corrected

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
    """
    try:
        # Set default time range if not provided
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
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
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
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
                "unique_zones": unique_zones,
                "score_statistics": {
                    "max_score": round(max_score, 2),
                    "min_score": round(min_score, 2),
                    "avg_score": round(avg_score, 2)
                },
                "first_anomaly": format_api_timestamp_corrected(first_anomaly) if first_anomaly else None,
                "last_anomaly": format_api_timestamp_corrected(last_anomaly) if last_anomaly else None,
                "has_anomalies": total_anomalies > 0
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_overview: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 1: Compact log anomaly list (basic info only, no raw data)
@mcp_server.tool()
async def get_log_anomalies_list(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 10,
    min_severity: str = "low"
) -> Dict[str, Any]:
    """
    Fetches a compact list of log anomalies with basic information only.
    Use this after getting the overview to see individual anomalies without overwhelming detail.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
        limit (int): Maximum number of anomalies to return (default: 10).
        min_severity (str): Minimum severity level - "low" (>0), "medium" (>10), "high" (>100).
    """
    try:
        # Set default time range if not provided
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
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
        
        # Filter by severity
        severity_thresholds = {"low": 0, "medium": 10, "high": 100}
        min_score = severity_thresholds.get(min_severity, 0)
        log_anomalies = [la for la in log_anomalies if la.get("anomalyScore", 0) >= min_score]
        
        # Sort by anomaly score (highest first) and limit
        log_anomalies = sorted(log_anomalies, key=lambda x: x.get("anomalyScore", 0), reverse=True)[:limit]

        # Create compact anomaly list
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
                "timestamp_human": format_api_timestamp_corrected(anomaly["timestamp"]),
                "component": anomaly.get("componentName", "Unknown"),
                "instance": anomaly.get("instanceName", "Unknown"),
                "pattern": anomaly.get("patternName", "Unknown"),
                "zone": anomaly.get("zoneName", "Unknown"),
                "anomaly_score": round(anomaly.get("anomalyScore", 0), 2),
                "severity": severity,
                "is_incident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0)
            }
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "min_severity": min_severity,
                "limit": limit
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
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

# Layer 2: Detailed log anomaly summary (includes root cause info but manageable)
@mcp_server.tool()
async def get_log_anomalies_summary(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    limit: int = 5,
    min_severity: str = "medium"
) -> Dict[str, Any]:
    """
    Fetches a detailed summary of log anomalies including root cause information.
    Use this when you need more detail about specific anomalies after reviewing the compact list.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
        limit (int): Maximum number of anomalies to return (default: 5).
        min_severity (str): Minimum severity level - "low" (>0), "medium" (>10), "high" (>100).
    """
    try:
        # Set default time range if not provided
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
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
        
        # Filter by severity
        severity_thresholds = {"low": 0, "medium": 10, "high": 100}
        min_score = severity_thresholds.get(min_severity, 10)
        log_anomalies = [la for la in log_anomalies if la.get("anomalyScore", 0) >= min_score]
        
        # Sort by anomaly score (highest first) and limit
        log_anomalies = sorted(log_anomalies, key=lambda x: x.get("anomalyScore", 0), reverse=True)[:limit]

        # Extract detailed summary information
        anomalies_summary = []
        for anomaly in log_anomalies:
            # Convert timestamp to human readable
            timestamp_str = format_api_timestamp_corrected(anomaly["timestamp"])
            
            # Determine severity category
            score = anomaly.get("anomalyScore", 0)
            if score > 100:
                severity = "high"
            elif score >= 10:
                severity = "medium"
            else:
                severity = "low"
            
            summary = {
                "anomaly_id": len(anomalies_summary) + 1,  # Simple ID for reference
                "timestamp": anomaly["timestamp"],
                "timestamp_human": timestamp_str,
                "instanceName": anomaly.get("instanceName", "Unknown"),
                "componentName": anomaly.get("componentName", "Unknown"),
                "patternName": anomaly.get("patternName", "Unknown"),
                "zoneName": anomaly.get("zoneName", "Unknown"),
                "anomalyScore": anomaly.get("anomalyScore", 0),
                "severity": severity,
                "isIncident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0),
                "has_raw_data": "rawData" in anomaly and anomaly["rawData"] is not None,
                "has_root_cause": "rootCauseResultInfo" in anomaly and anomaly["rootCauseResultInfo"] is not None,
            }
            
            # Add root cause summary if available
            if "rootCauseResultInfo" in anomaly and anomaly["rootCauseResultInfo"]:
                root_cause = anomaly["rootCauseResultInfo"]
                summary["root_cause_summary"] = {
                    "hasPrecedingEvent": root_cause.get("hasPrecedingEvent", False),
                    "hasTrailingEvent": root_cause.get("hasTrailingEvent", False),
                    "causedByChangeEvent": root_cause.get("causedByChangeEvent", False),
                    "leadToIncident": root_cause.get("leadToIncident", False)
                }
            
            # Add a preview of raw data if available (first 200 chars)
            if "rawData" in anomaly and anomaly["rawData"]:
                raw_data = str(anomaly["rawData"])
                summary["raw_data_preview"] = raw_data[:200] + "..." if len(raw_data) > 200 else raw_data
                summary["raw_data_length"] = len(raw_data)
            
            anomalies_summary.append(summary)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "min_severity": min_severity,
                "limit": limit
            },
            "time_range": {
                "start": start_time_ms,
                "end": end_time_ms,
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
            },
            "total_found": len(result["data"]),
            "returned_count": len(anomalies_summary),
            "anomalies": anomalies_summary
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_summary: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 3: Full log anomaly details (without raw data)
@mcp_server.tool()
async def get_log_anomaly_details(
    system_name: str,
    anomaly_timestamp: int,
    include_root_cause: bool = True
) -> Dict[str, Any]:
    """
    Fetches complete information about a specific log anomaly, excluding raw data to keep response manageable.
    Use this after identifying a specific anomaly from the list or summary layers.

    Args:
        system_name (str): The name of the system to query.
        anomaly_timestamp (int): The timestamp of the specific anomaly to get details for.
        include_root_cause (bool): Whether to include detailed root cause information.
    """
    try:
        # Get log anomalies for a small time window around the specific timestamp
        start_time = anomaly_timestamp - (5 * 60 * 1000)  # 5 minutes before
        end_time = anomaly_timestamp + (5 * 60 * 1000)    # 5 minutes after

        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time,
            end_time_ms=end_time,
        )

        if result["status"] != "success":
            return result

        # Find the specific anomaly
        target_anomaly = None
        for anomaly in result["data"]:
            if anomaly["timestamp"] == anomaly_timestamp:
                target_anomaly = anomaly
                break

        if not target_anomaly:
            return {"status": "error", "message": f"Log anomaly with timestamp {anomaly_timestamp} not found"}

        # Build detailed response without raw data
        anomaly_details = {
            "timestamp": target_anomaly["timestamp"],
            "timestamp_human": format_api_timestamp_corrected(target_anomaly["timestamp"]),
            "instanceName": target_anomaly.get("instanceName"),
            "componentName": target_anomaly.get("componentName"),
            "patternName": target_anomaly.get("patternName"),
            "zoneName": target_anomaly.get("zoneName"),
            "anomalyScore": target_anomaly.get("anomalyScore"),
            "isIncident": target_anomaly.get("isIncident"),
            "active": target_anomaly.get("active"),
        }

        # Add root cause details if requested and available
        if include_root_cause and "rootCauseResultInfo" in target_anomaly and target_anomaly["rootCauseResultInfo"]:
            anomaly_details["rootCauseResultInfo"] = target_anomaly["rootCauseResultInfo"]

        return {
            "status": "success",
            "anomaly_details": anomaly_details,
            "has_raw_data": "rawData" in target_anomaly and target_anomaly["rawData"] is not None
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomaly_details: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 4: Raw data extraction (for deep investigation)
@mcp_server.tool()
async def get_log_anomaly_raw_data(
    system_name: str,
    anomaly_timestamp: int,
    max_length: int = 5000
) -> Dict[str, Any]:
    """
    Fetches the raw log data for a specific log anomaly.
    Use this only when you need to examine the actual log content.

    Args:
        system_name (str): The name of the system to query.
        anomaly_timestamp (int): The timestamp of the specific anomaly.
        max_length (int): Maximum length of raw data to return (to prevent overwhelming the LLM).
    """
    try:
        # Get log anomalies for a small time window around the specific timestamp
        start_time = anomaly_timestamp - (5 * 60 * 1000)  # 5 minutes before
        end_time = anomaly_timestamp + (5 * 60 * 1000)    # 5 minutes after

        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time,
            end_time_ms=end_time,
        )

        if result["status"] != "success":
            return result

        # Find the specific anomaly
        target_anomaly = None
        for anomaly in result["data"]:
            if anomaly["timestamp"] == anomaly_timestamp:
                target_anomaly = anomaly
                break

        if not target_anomaly:
            return {"status": "error", "message": f"Log anomaly with timestamp {anomaly_timestamp} not found"}

        raw_data = target_anomaly.get("rawData", "")
        if not raw_data:
            return {"status": "error", "message": "No raw data available for this log anomaly"}

        # Convert to string if it's not already
        raw_data = str(raw_data)

        # Truncate if too long
        if len(raw_data) > max_length:
            raw_data = raw_data[:max_length] + f"\n... [TRUNCATED - Full length: {len(target_anomaly['rawData'])} characters]"

        return {
            "status": "success",
            "anomaly_timestamp": anomaly_timestamp,
            "timestamp_human": format_api_timestamp_corrected(anomaly_timestamp),
            "instanceName": target_anomaly.get("instanceName"),
            "componentName": target_anomaly.get("componentName"),
            "patternName": target_anomaly.get("patternName"),
            "raw_data": raw_data,
            "raw_data_length": len(str(target_anomaly.get("rawData", ""))),
            "truncated": len(str(target_anomaly.get("rawData", ""))) > max_length
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomaly_raw_data: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 5: Statistics and analysis tools
@mcp_server.tool()
async def get_log_anomalies_statistics(
    system_name: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Provides statistical analysis of log anomalies for a system over a time period.
    Use this to understand anomaly patterns, frequency, and impact.

    Args:
        system_name (str): The name of the system to analyze.
        start_time_ms (int): Optional. The start of the time window in Unix timestamp (milliseconds).
        end_time_ms (int): Optional. The end of the time window in Unix timestamp (milliseconds).
    """
    try:
        # Set default time range if not provided
        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_timezone_aware_time_range_ms(1)
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
        
        # Calculate statistics
        total_anomalies = len(log_anomalies)
        
        # Severity distribution
        high_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) > 100])
        medium_severity = len([la for la in log_anomalies if 10 <= la.get("anomalyScore", 0) <= 100])
        low_severity = len([la for la in log_anomalies if la.get("anomalyScore", 0) < 10])
        
        # Group by component, instance, pattern, and zone
        components = {}
        instances = {}
        patterns = {}
        zones = {}
        
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
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms),
                "end_human": format_timestamp_in_user_timezone(end_time_ms)
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
                "top_zones": dict(sorted(zones.items(), key=lambda x: x[1], reverse=True)[:10]) if zones else {}
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_statistics: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}
