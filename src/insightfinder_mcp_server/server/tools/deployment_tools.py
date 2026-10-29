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
from ...config.settings import settings
from ...api_client.client_factory import get_current_api_client
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    format_timestamp_in_user_timezone,
    parse_time_parameters
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
# SERVICENOW TICKET LOOKUP
# ============================================================================

# How much of one ticket is returned: one field, and all fields together. ServiceNow records are
# mostly empty fields in arbitrary order, so the empty ones are dropped and the fields that say
# what changed and when go first; what is left over the budget is named in omitted_fields.
_MAX_TICKET_FIELD_CHARS = 2000
_MAX_TICKET_CONTENT_CHARS = 10000
# Fields of ServiceNow change requests (CHG) and problems (PRB), most telling first.
_TICKET_PRIORITY_FIELDS = [
    "number", "ticket number", "short_description", "description", "type", "category", "state",
    "phase", "risk", "impact", "priority",
    "start_date", "end_date", "work_start", "work_end", "u_planned_start_copy", "u_planned_end_copy",
    "opened_at", "closed_at", "close_code", "close_notes",
    "cmdb_ci", "business_service", "u_impacted_services", "assignment_group", "assigned_to",
    "requested_by", "implementation_plan", "justification", "backout_plan", "test_plan", "reason",
    "problem_statement", "cause_notes", "fix_notes", "workaround", "known_error",
    "sys_updated_on",
]


@mcp_server.tool()
async def get_servicenow_ticket_timeline(
    ticket_number: str,
    system_name: str,
    customer_name: Optional[str] = None,
    incident_timestamp: Optional[Union[str, int]] = None,
    lookback_days: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Look up a ServiceNow ticket by its number and return the change event (deployment) or problem
    record InsightFinder stored for it, including its full content.

    Use this whenever a user, a feedback note or an incident mentions a ServiceNow ticket number
    such as CHG0829429 (change request) or PRB0012345 (problem), e.g. "this incident is caused by
    change event CHG0829429", to learn what the change did, where and when.

    Reading a change ticket:
    - WHEN it ran comes from the ticket's own fields: start_date / end_date is the planned
      window, work_start / work_end the actual one. recorded_at, opened_at and sys_created_on
      are only when the ticket was created, often days or weeks earlier; never report them as
      the deploy time. When relating the ticket to an incident, say whether the incident falls
      inside that window, shortly after it, days after it, or before it.
    - test_plan describes pre-implementation testing, not the outcome of the change itself.
    - Ticket times are quoted as the ticket stores them.
    - Tickets are found by the day they were created, so a change created long before the
      incident needs a larger lookback_days.
    - event_time (when the change ran, MM/dd/yyyy HH:mm:ss), data_source, instance_name and
      link (the ticket's ServiceNow URL, when known) are what summaries cite for this ticket. Whenever you report a ticket's findings, including to
      another agent, always pass on these three values exactly as returned.

    Args:
        ticket_number: The ServiceNow ticket number, e.g. "CHG0829429".
        system_name: The system id (hash) or the system's display name.
        customer_name: The user who owns the system. Defaults to the current user.
        incident_timestamp: Where to start searching back from, usually the incident time; epoch
            milliseconds or a human-readable time. Defaults to now.
        lookback_days: How many days before incident_timestamp to search. Defaults to the
            server's SERVICENOW_TICKET_LOOKBACK_DAYS (30). Widen it when a ticket is not found;
            change requests are often implemented days or weeks before the incident they cause.

    Returns:
        {"status": "success", "ticket_number", "system_id", "events": [{event_time, event_type, data_source,
        instance_name, component_name, pattern_name, recorded_at, link, fields, omitted_fields}]},
        or {"status": "not_found"} / {"status": "error", "message"}. `fields` holds the ticket's
        non-empty ServiceNow fields, the telling ones first: for when the change actually ran use
        start_date/end_date (planned window) and work_start/work_end (actual). `recorded_at` is only when the ticket was recorded
        (usually its creation), not when the change happened.
    """
    try:
        ticket_number = (ticket_number or "").strip()
        if not ticket_number:
            return {"status": "error", "message": "ticket_number is required"}
        if lookback_days is not None and lookback_days < 0:
            return {"status": "error", "message": "lookback_days must not be negative"}
        client = _get_api_client()
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if incident_timestamp is not None and str(incident_timestamp).strip():
            if str(incident_timestamp).strip().isdigit():
                timestamp_ms = int(str(incident_timestamp).strip())
            else:
                try:
                    timestamp_ms, _ = parse_time_parameters(incident_timestamp, None, "UTC")
                except ValueError as e:
                    return {"status": "error", "message": str(e)}

        days = lookback_days if lookback_days is not None else settings.SERVICENOW_TICKET_LOOKBACK_DAYS
        raw = await client.get_servicenow_ticket_timeline(
            customer_name=customer_name or client.user_name,
            system_name=system_name,
            ticket_number=ticket_number,
            start_time_ms=timestamp_ms - days * 86_400_000,
            end_time_ms=timestamp_ms,
        )
        if raw.get("status") == "not_found":
            return {
                "status": "not_found",
                "ticket_number": ticket_number,
                "searched_days": days,
                "message": f"Ticket {ticket_number} was not found in the {days} days searched; "
                           f"try a larger lookback_days or another incident_timestamp.",
            }
        if raw.get("status") != "success":
            return raw

        events = []
        for tl in raw.get("timelines", []):
            fields, omitted = _ticket_fields(tl.get("rawData") or "")
            event = {
                # When the change ran, formatted here (not by the model) as summaries show it.
                "event_time": _event_time(tl, fields),
                "event_type": tl.get("eventType"),
                # The InsightFinder project the event came from: what summaries show as
                # "[Data source: ...]".
                "data_source": tl.get("projectName"),
                "instance_name": tl.get("instanceName"),
                "component_name": tl.get("componentName"),
                "pattern_name": tl.get("patternName"),
                # When InsightFinder recorded the ticket (usually its creation), NOT when the
                # change ran: that is start_date/end_date (planned), work_start/work_end (actual).
                "recorded_at": _format_utc(tl.get("startTimestamp")),
                "link": tl.get("hyperLink"),
                "fields": fields,
            }
            if omitted:
                event["omitted_fields"] = omitted
            events.append(event)
        number = raw.get("ticketNumber", ticket_number)
        # Every event must carry its data source: summaries cite it, and the coordinator gets it
        # only through exact_values.
        if not events:
            return {"status": "not_found", "ticket_number": number,
                    "message": f"Ticket {number} was not found."}
        if any(not e["data_source"] for e in events):
            return {"status": "error",
                    "message": f"Ticket {number} was found but has no data source."}
        return {
            "status": "success",
            "ticket_number": number,
            "system_id": raw.get("systemId"),
            "events": events,
            # Read by mcp-service's coordinator, which appends these to the sub-agent's answer
            # unchanged, so they survive the multi-agent hand-off (app/utils/ExactValues.py).
            "exact_values": [{
                "ticket_number": number,
                "event_time": e["event_time"],
                "data_source": e["data_source"],
                "instance_name": e["instance_name"],
                "link": e["link"],
            } for e in events],
        }
    except Exception as e:
        logger.error(f"get_servicenow_ticket_timeline failed for {ticket_number}: {e}")
        return {"status": "error", "message": f"Failed to look up ticket {ticket_number}: {str(e)}"}


def _ticket_fields(raw_data: str):
    """(fields, omitted names) of a ticket body: the non-empty fields, priority fields first, each
    cut at _MAX_TICKET_FIELD_CHARS, until _MAX_TICKET_CONTENT_CHARS. A body that is not a JSON
    object comes back whole under "content", cut at the total budget."""
    try:
        body = json.loads(raw_data) if raw_data else {}
    except (ValueError, TypeError):
        body = None
    if not isinstance(body, dict):
        text = str(raw_data)
        cut = len(text) > _MAX_TICKET_CONTENT_CHARS
        return {"content": text[:_MAX_TICKET_CONTENT_CHARS] + (" ...[truncated]" if cut else "")}, []

    def text_of(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    present = {k: text_of(v).strip() for k, v in body.items()
               if v is not None and text_of(v).strip() not in ("", "{}", "[]")}
    lower = {k.lower(): k for k in present}
    ordered = [lower[p] for p in _TICKET_PRIORITY_FIELDS if p in lower]
    ordered += [k for k in present if k not in ordered]

    fields, omitted, used = {}, [], 0
    for key in ordered:
        value = present[key]
        if len(value) > _MAX_TICKET_FIELD_CHARS:
            value = value[:_MAX_TICKET_FIELD_CHARS] + " ...[truncated]"
        if used + len(key) + len(value) > _MAX_TICKET_CONTENT_CHARS:
            omitted.append(key)
            continue
        fields[key] = value
        used += len(key) + len(value)
    return fields, omitted


def _event_time(timeline: Dict[str, Any], fields: Dict[str, str]) -> Optional[str]:
    """When the change ran as "MM/dd/yyyy HH:mm:ss": the planned start, else the actual start,
    else when the ticket was recorded. Ticket times are taken as the ticket stores them."""
    when = None
    for key in ("start_date", "work_start"):
        try:
            when = datetime.strptime(fields.get(key, "")[:19], "%Y-%m-%d %H:%M:%S")
            break
        except ValueError:
            continue
    if when is None and timeline.get("startTimestamp"):
        when = datetime.fromtimestamp(timeline["startTimestamp"] / 1000, tz=timezone.utc)
    return when.strftime("%m/%d/%Y %H:%M:%S") if when is not None else None


def _format_utc(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


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

