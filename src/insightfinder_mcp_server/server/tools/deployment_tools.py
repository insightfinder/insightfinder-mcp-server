"""
Streamlined deployment tools for the InsightFinder MCP server.

This module provides a focused approach for exploring deployments,
also known as "change events" in InsightFinder terminology. These terms are used
interchangeably - "show me deployments" is equivalent to "show me change events".

The tools offer simplified layers of detail:
- Layer 0: Ultra-compact overview (get_deployments_overview)
- Layer 1: Enhanced list with detailed information (get_deployments_list)
- Layer 2: Statistics and analysis (get_deployments_statistics)
- Project-specific: Project-filtered deployments (get_project_deployments)

Each layer provides increasingly detailed information while maintaining LLM-friendly,
structured outputs optimized for analysis and reasoning. All tools support optional
project_name filtering to focus on specific projects within a system.

Note: Deployments and change events refer to the same data in InsightFinder - events
that represent changes to your system such as code deployments, configuration changes,
infrastructure modifications, etc.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone
import re

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    format_timestamp_in_user_timezone,
    convert_to_ms,
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

logger = logging.getLogger(__name__)

# ============================================================================
# LAYER 0: ULTRA-COMPACT OVERVIEW
# ============================================================================

@mcp_server.tool()
async def get_deployments_overview(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Layer 0: Ultra-compact overview of deployments (change events).
    
    Provides the most condensed view possible - just essential counts and high-level patterns.
    Perfect for initial assessment and determining if deeper investigation is needed.
    
    Note: Deployments and change events are the same data in InsightFinder - this tool
    shows change events such as code deployments, configuration changes, etc.
    
    Args:
        system_name: Name of the system to query
        start_time: Start timestamp. Accepts human-readable formats or milliseconds.
        end_time: End timestamp. Accepts human-readable formats or milliseconds.
        project_name: Optional project name to filter deployments (client-side filtering)
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

        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        # Check for deployments in the new response structure (data array) or legacy structure (timelineList)
        deployments_data = raw_data.get("data") or raw_data.get("timelineList")
        
        if not deployments_data:
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "summary": {
                    "total_deployments": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        deployments = deployments_data
        
        # Apply project_name filter if specified
        if project_name:
            # deployments = [d for d in deployments if d.get("projectName") == project_name]
            deployments = [d for d in deployments if d.get("projectName", "").lower() == project_name.lower() or d.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Check if no deployments after filtering
        if not deployments:
            filter_msg = f" (filtered by project: {project_name})" if project_name else ""
            return {
                "status": "success",
                "message": f"No deployments found in the specified time range{filter_msg}",
                "systemName": system_name,
                "projectName": project_name,
                "summary": {
                    "total_deployments": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        # Extract key metrics
        total_deployments = len(deployments)
        
        # Parse deployment data and collect projects
        job_types = set()
        build_statuses = {"SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}
        components = set()
        instances = set()
        patterns = set()
        projects = set()
        
        for deployment in deployments:
            # Collect project names
            if deployment.get("projectDisplayName"):
                projects.add(deployment["projectDisplayName"])
            
            # Parse raw data
            raw_data_str = deployment.get("rawData", "")
            job_type, build_status = _parse_deployment_raw_data(raw_data_str)
            
            if job_type:
                job_types.add(job_type)
            
            if build_status in build_statuses:
                build_statuses[build_status] += 1
            else:
                build_statuses["UNKNOWN"] += 1
            
            if deployment.get("componentName"):
                components.add(deployment["componentName"])
            if deployment.get("instanceName"):
                instances.add(deployment["instanceName"])
            if deployment.get("patternName"):
                patterns.add(deployment["patternName"])
        
        # Calculate time span
        timestamps = [d.get("timestamp", 0) for d in deployments if d.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        # Calculate success rate
        total_with_status = sum(build_statuses.values())
        success_rate = (build_statuses["SUCCESS"] / total_with_status * 100) if total_with_status > 0 else 0
        
        # Top job types
        job_type_counts = {}
        for deployment in deployments:
            raw_data_str = deployment.get("rawData", "")
            job_type, _ = _parse_deployment_raw_data(raw_data_str)
            if job_type:
                job_type_counts[job_type] = job_type_counts.get(job_type, 0) + 1
        
        top_job_types = sorted(job_type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        return {
            "status": "success",
            "systemName": system_name,
            "projectName": project_name,
            "summary": {
                "total_deployments": total_deployments,
                "build_status_distribution": build_statuses,
                "success_rate_percentage": round(success_rate, 1),
                "unique_components": len(components),
                "unique_instances": len(instances),
                "unique_job_types": len(job_types),
                "unique_patterns": len(patterns),
                "unique_projects": len(projects),
                "time_span_hours": time_span_hours,
                "top_job_types": [{"job_type": jt, "count": c} for jt, c in top_job_types],
                "time_range": {
                    "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                    "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_overview: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments overview: {str(e)}"
        }

# ============================================================================
# LAYER 1: ENHANCED LIST WITH DETAILED INFORMATION
# ============================================================================

@mcp_server.tool()
async def get_deployments_list(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20,
    build_status: Optional[str] = None,
    job_type: Optional[str] = None,
    project_name: Optional[str] = None,
    sort_by: str = "timestamp",
    include_raw_data: bool = False
) -> Dict[str, Any]:
    """
    Enhanced deployment list with comprehensive information.
    This is the main tool for getting deployment details - combines basic info with detailed data.
    
    Note: This shows the same data whether you think of it as "deployments" or "change events" -
    they refer to the same InsightFinder data representing system changes.
    
    Args:
        system_name: Name of the system to query
        start_time: Start timestamp. Accepts human-readable formats or milliseconds.
        end_time: End timestamp. Accepts human-readable formats or milliseconds.
        limit: Maximum number of deployments to return
        build_status: Filter by build status ("SUCCESS", "FAILURE")
        job_type: Filter by job type (e.g., "WEB", "CORE", "API")
        project_name: Optional project name to filter deployments (client-side filtering)
        sort_by: Sort field ("timestamp", "status", "job_type")
        include_raw_data: Whether to include full raw deployment data (default: False for performance)
        include_analysis: Whether to include deployment analysis (default: True)
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

        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )

        # Check for deployments in the new response structure (data array) or legacy structure (timelineList)
        deployments_data = raw_data.get("data") or raw_data.get("timelineList")
        
        if not deployments_data:
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "systemName": system_name,
                "projectName": project_name,
                "total_found": 0,
                "returned_count": 0,
                "deployments": []
            }
        
        deployments = deployments_data
        
        # Filter deployments
        filtered_deployments = []
        for deployment in deployments:
            # Apply project_name filter first
            if project_name and deployment.get("projectName") != project_name and deployment.get("projectDisplayName") != project_name:
                continue
                
            raw_data_str = deployment.get("rawData", "")
            parsed_job_type, parsed_status = _parse_deployment_raw_data(raw_data_str)
            
            # Apply filters
            if build_status and parsed_status != build_status:
                continue
            if job_type and parsed_job_type != job_type:
                continue
                
            filtered_deployments.append(deployment)
        
        # Sort deployments
        if sort_by == "status":
            filtered_deployments.sort(key=lambda x: _parse_deployment_raw_data(x.get("rawData", ""))[1] or "", reverse=True)
        elif sort_by == "job_type":
            filtered_deployments.sort(key=lambda x: _parse_deployment_raw_data(x.get("rawData", ""))[0] or "")
        else:  # timestamp
            filtered_deployments.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit results
        limited_deployments = filtered_deployments[:limit]
        
        # Create enhanced representation
        enhanced_deployments = []
        for deployment in limited_deployments:
            raw_data_str = deployment.get("rawData", "")
            job_type_parsed, build_status_parsed = _parse_deployment_raw_data(raw_data_str)
            result_info = deployment.get("rootCauseResultInfo", {})
            
            enhanced_deployment = {
                "timestamp": deployment.get("timestamp"),
                "datetime": format_timestamp_in_user_timezone(deployment.get("timestamp", 0, tz_name)) if deployment.get("timestamp") else None,
                "active": deployment.get("active", 0),
                
                # Location information
                "location": {
                    "project_name": deployment.get("projectDisplayName"),
                    "component": deployment.get("componentName"),
                    "instance": deployment.get("instanceName")
                },
                
                # Deployment information
                "deployment": {
                    "job_type": job_type_parsed,
                    "build_status": build_status_parsed,
                    "pattern_name": deployment.get("patternName"),
                    "anomaly_score": deployment.get("anomalyScore", 0.0)
                },
                
                # System status
                "system_status": {
                    "is_incident": deployment.get("isIncident", False),
                    "active": deployment.get("active", 0)
                },
                
                # Context information
                "context": {
                    "has_preceding_event": result_info.get("hasPrecedingEvent", False),
                    "has_trailing_event": result_info.get("hasTrailingEvent", False),
                    "caused_by_change_event": result_info.get("causedByChangeEvent", False),
                    "lead_to_incident": result_info.get("leadToIncident", False)
                }
            }
            
            # Add raw data if requested and available
            if include_raw_data:
                enhanced_deployment["raw_data"] = {
                    "full_data": deployment,
                    "raw_deployment_data": raw_data_str,
                    "parsed_details": _parse_detailed_raw_data(raw_data_str)
                }
                enhanced_deployment["raw_data_length"] = len(json.dumps(deployment))
            elif raw_data_str:
                # Always include a preview even if full raw data not requested
                enhanced_deployment["raw_data_preview"] = {
                    "preview": raw_data_str[:100] + "..." if len(raw_data_str) > 100 else raw_data_str,
                    "parsed_summary": {"job_type": job_type_parsed, "build_status": build_status_parsed},
                    "has_full_raw_data": True,
                    "raw_data_length": len(raw_data_str)
                }
            
            enhanced_deployments.append(enhanced_deployment)
        
        return {
            "status": "success",
            "systemName": system_name,
            "projectName": project_name,
            "total_found": len(filtered_deployments),
            "returned_count": len(enhanced_deployments),
            "filters": {
                "build_status": build_status,
                "job_type": job_type,
                "project_name": project_name,
                "sort_by": sort_by,
                "limit": limit,
                "include_raw_data": include_raw_data
            },
            "deployments": enhanced_deployments
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_list: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments list: {str(e)}"
        }

# ============================================================================
# LAYER 2: STATISTICS AND ANALYSIS
# ============================================================================

@mcp_server.tool()
async def get_deployments_statistics(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None,
    include_trends: bool = True
) -> Dict[str, Any]:
    """
    Layer 5: Comprehensive statistics for deployments (change events).
    
    Provides statistical analysis, trends, and insights across all deployments/change events
    in the time range. Good for understanding patterns and overall system change health.
    
    Note: This analyzes change event statistics - the same data whether you think of them
    as deployments, change events, or system modifications in InsightFinder.
    
    Args:
        system_name: Name of the system to query
        start_time: Start timestamp. Accepts human-readable formats or milliseconds.
        end_time: End timestamp. Accepts human-readable formats or milliseconds.
        project_name: Optional project name to filter deployments (client-side filtering)
        include_trends: Whether to include trend analysis
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

        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        # Check for deployments in the new response structure (data array) or legacy structure (timelineList)
        deployments_data = raw_data.get("data") or raw_data.get("timelineList")
        
        if not deployments_data:
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "systemName": system_name,
                "projectName": project_name,
                "statistics": {
                    "total_deployments": 0,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        deployments = deployments_data
        
        # Apply project_name filter if specified
        if project_name:
            # deployments = [d for d in deployments if d.get("projectName") == project_name]
            deployments = [d for d in deployments if d.get("projectName", "").lower() == project_name.lower() or d.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Basic statistics
        total_deployments = len(deployments)
        
        # Build status analysis
        build_status_counts = {"SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}
        job_type_counts = {}
        component_counts = {}
        instance_counts = {}
        pattern_counts = {}
        project_counts = {}
        
        # Anomaly score analysis
        anomaly_scores = []
        incident_count = 0
        
        for deployment in deployments:
            # Project tracking
            project = deployment.get("projectDisplayName", "Unknown")
            project_counts[project] = project_counts.get(project, 0) + 1
            
            raw_data_str = deployment.get("rawData", "")
            job_type, build_status = _parse_deployment_raw_data(raw_data_str)
            
            # Build status tracking
            if build_status in build_status_counts:
                build_status_counts[build_status] += 1
            else:
                build_status_counts["UNKNOWN"] += 1
            
            # Job type tracking
            if job_type:
                job_type_counts[job_type] = job_type_counts.get(job_type, 0) + 1
            
            # Component tracking
            component = deployment.get("componentName", "Unknown")
            component_counts[component] = component_counts.get(component, 0) + 1
            
            # Instance tracking
            instance = deployment.get("instanceName", "Unknown")
            instance_counts[instance] = instance_counts.get(instance, 0) + 1
            
            # Pattern tracking
            pattern = deployment.get("patternName", "Unknown")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            
            # Anomaly scores
            score = deployment.get("anomalyScore", 0.0)
            anomaly_scores.append(score)
            
            # Incident count
            if deployment.get("isIncident"):
                incident_count += 1
        
        # Calculate success rate
        total_with_status = build_status_counts["SUCCESS"] + build_status_counts["FAILURE"]
        success_rate = (build_status_counts["SUCCESS"] / total_with_status * 100) if total_with_status > 0 else 0
        
        # Calculate percentages and top items
        def get_top_items(counts_dict, top_n=5):
            sorted_items = sorted(counts_dict.items(), key=lambda x: x[1], reverse=True)
            return {
                item: {"count": count, "percentage": round(count / total_deployments * 100, 1)}
                for item, count in sorted_items[:top_n]
            }
        
        # Statistical calculations
        avg_score = sum(anomaly_scores) / len(anomaly_scores) if anomaly_scores else 0
        max_score = max(anomaly_scores) if anomaly_scores else 0
        min_score = min(anomaly_scores) if anomaly_scores else 0
        
        # Time analysis
        timestamps = [d.get("timestamp", 0) for d in deployments if d.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        statistics = {
            "total_deployments": total_deployments,
            "time_range": {
                "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1),
                "actual_span_hours": time_span_hours
            },
            
            "build_analysis": {
                "status_distribution": build_status_counts,
                "success_rate_percentage": round(success_rate, 1),
                "failure_count": build_status_counts["FAILURE"],
                "unknown_count": build_status_counts["UNKNOWN"]
            },
            
            "job_analysis": {
                "unique_job_types": len(job_type_counts),
                "top_job_types": get_top_items(job_type_counts),
                "job_type_distribution": job_type_counts
            },
            
            "infrastructure_analysis": {
                "unique_components": len(component_counts),
                "unique_instances": len(instance_counts),
                "unique_patterns": len(pattern_counts),
                "unique_projects": len(project_counts),
                "top_components": get_top_items(component_counts),
                "top_instances": get_top_items(instance_counts),
                "pattern_distribution": get_top_items(pattern_counts),
                "top_affected_projects": get_top_items(project_counts)
            },
            
            "anomaly_analysis": {
                "average_score": round(avg_score, 3),
                "max_score": max_score,
                "min_score": min_score,
                "deployments_with_incidents": incident_count,
                "incident_rate_percentage": round(incident_count / total_deployments * 100, 1) if total_deployments > 0 else 0
            }
        }
        
        
        return {
            "status": "success",
            "systemName": system_name,
            "projectName": project_name,
            "statistics": statistics
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_statistics: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments statistics: {str(e)}"
        }

# ============================================================================
# PROJECT-SPECIFIC FUNCTION
# ============================================================================

@mcp_server.tool()
async def get_project_deployments(
    system_name: str,
    project_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Fetches deployments (change events) specifically for a given project within a system.
    Use this tool when the user specifies both a system name and project name.
    
    Note: This shows deployment/change event data - both terms refer to the same InsightFinder
    data representing system changes like code deployments, configuration changes, etc.
    
    Example usage:
    - "show me deployments for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get deployments before incident for project X in system Y"
    - "what change events happened in project ABC"

    Args:
        system_name (str): The name of the system (e.g., "InsightFinder Demo System (APP)")
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time: Start timestamp. Accepts human-readable formats or milliseconds.
        end_time: End timestamp. Accepts human-readable formats or milliseconds.
        limit (int): Maximum number of deployments to return (default: 20)
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

        client = _get_api_client()
        
        # Call the InsightFinder API client with ONLY the system name
        result = await client.get_deployment(
            system_name=system_name,  # Use only the system name here
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        # Check for deployments in the new response structure (data array) or legacy structure (timelineList)
        deployments_data = result.get("data") or result.get("timelineList")
        
        if not deployments_data:
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "summary": {
                    "total_deployments": 0,
                    "project_name": project_name,
                    "system_name": system_name,
                    "time_range": {
                        "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                        "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }

        deployments = deployments_data
        
        # Filter by the specific project name
        # project_deployments = [d for d in deployments if d.get("projectName") == project_name]
        project_deployments = [d for d in deployments if d.get("projectName", "").lower() == project_name.lower() or d.get("projectDisplayName", "").lower() == project_name.lower()]
        
        # Sort by timestamp (most recent first) and limit
        project_deployments = sorted(project_deployments, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]

        # Create detailed deployment list for the project
        deployment_list = []
        for i, deployment in enumerate(project_deployments):
            raw_data_str = deployment.get("rawData", "")
            job_type, build_status = _parse_deployment_raw_data(raw_data_str)
            result_info = deployment.get("rootCauseResultInfo", {})
            
            deployment_summary = {
                "index": i + 1,
                "timestamp": deployment.get("timestamp"),
                "datetime": format_timestamp_in_user_timezone(deployment.get("timestamp", 0, tz_name)) if deployment.get("timestamp") else None,
                "active": deployment.get("active", 0),
                
                # Location information
                "project_name": project_name,
                "component_name": deployment.get("componentName"),
                "instance_name": deployment.get("instanceName"),
                
                # Deployment information
                "pattern_name": deployment.get("patternName"),
                "job_type": job_type,
                "build_status": build_status,
                "anomaly_score": deployment.get("anomalyScore", 0.0),
                
                # System status flags
                "is_incident": deployment.get("isIncident", False),
                
                # Context information
                "has_preceding_event": result_info.get("hasPrecedingEvent", False),
                "has_trailing_event": result_info.get("hasTrailingEvent", False),
                "caused_by_change_event": result_info.get("causedByChangeEvent", False),
                "lead_to_incident": result_info.get("leadToIncident", False),
                
                # Raw deployment data preview
                "raw_data_preview": raw_data_str[:100] + "..." if len(raw_data_str) > 100 else raw_data_str,
                "raw_data_length": len(raw_data_str)
            }
            
            deployment_list.append(deployment_summary)

        # Summary statistics
        total_deployments = len(project_deployments)
        build_status_counts = {"SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}
        job_type_counts = {}
        incident_count = 0
        
        for deployment in project_deployments:
            raw_data_str = deployment.get("rawData", "")
            job_type, build_status = _parse_deployment_raw_data(raw_data_str)
            
            # Build status tracking
            if build_status in build_status_counts:
                build_status_counts[build_status] += 1
            else:
                build_status_counts["UNKNOWN"] += 1
            
            # Job type tracking  
            if job_type:
                job_type_counts[job_type] = job_type_counts.get(job_type, 0) + 1
                
            # Incident count
            if deployment.get("isIncident"):
                incident_count += 1

        # Calculate success rate
        total_with_status = build_status_counts["SUCCESS"] + build_status_counts["FAILURE"]
        success_rate = (build_status_counts["SUCCESS"] / total_with_status * 100) if total_with_status > 0 else 0

        return {
            "status": "success",
            "message": f"Found {total_deployments} deployments for project '{project_name}' in system '{system_name}'",
            "summary": {
                "total_deployments": total_deployments,
                "build_status_distribution": build_status_counts,
                "success_rate_percentage": round(success_rate, 1),
                "job_type_distribution": job_type_counts,
                "deployments_with_incidents": incident_count,
                "incident_rate_percentage": round(incident_count / total_deployments * 100, 1) if total_deployments > 0 else 0,
                "project_name": project_name,
                "system_name": system_name,
                "timezone": tz_name,
                "time_range": {
                    "start": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                    "end": format_timestamp_in_user_timezone(end_time_ms, tz_name),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            },
            "deployments": deployment_list
        }

    except Exception as e:
        error_message = f"Error in get_project_deployments: {str(e)}"
        logger.error(error_message)
        return {"status": "error", "message": error_message}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _parse_deployment_raw_data(raw_data: str) -> tuple[Optional[str], Optional[str]]:
    """Parse deployment raw data to extract job type and build status."""
    if not raw_data:
        return None, None
    
    job_type = None
    build_status = None
    
    # Parse job type
    job_type_match = re.search(r'jobType:\s*(\w+)', raw_data)
    if job_type_match:
        job_type = job_type_match.group(1)
    
    # Parse build status
    status_match = re.search(r'buildStatus:\s*(\w+)', raw_data)
    if status_match:
        build_status = status_match.group(1)
    
    return job_type, build_status

def _parse_detailed_raw_data(raw_data: str) -> Dict[str, Any]:
    """Parse deployment raw data to extract all available details."""
    if not raw_data:
        return {}
    
    details = {}
    
    # Parse all key-value pairs
    lines = raw_data.strip().split('\n')
    for line in lines:
        if ':' in line:
            key, value = line.split(':', 1)
            details[key.strip()] = value.strip()
    
    return details


def _calculate_deployment_summary_stats(deployments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics for a list of deployments."""
    if not deployments:
        return {}
    
    total = len(deployments)
    success_count = 0
    failure_count = 0
    incident_count = 0
    
    for deployment in deployments:
        build_status = deployment.get("deployment", {}).get("build_status")
        if build_status == "SUCCESS":
            success_count += 1
        elif build_status == "FAILURE":
            failure_count += 1
        
        if deployment.get("system_status", {}).get("is_incident"):
            incident_count += 1
    
    return {
        "total_deployments": total,
        "success_count": success_count,
        "failure_count": failure_count,
        "incident_count": incident_count,
        "success_rate_percentage": round(success_count / total * 100, 1) if total > 0 else 0
    }

