"""
Multi-layered deployment tools for the InsightFinder MCP server.

This module provides a progressive drill-down approach for exploring deployments:
- Layer 0: Ultra-compact overview (get_deployments_overview)
- Layer 1: Compact list (get_deployments_list) 
- Layer 2: Detailed summary (get_deployments_summary)
- Layer 3: Full details (get_deployment_details)
- Layer 4: Raw data (get_deployment_raw_data)
- Layer 5: Statistics (get_deployments_statistics)

Each layer provides increasingly detailed information while maintaining LLM-friendly,
structured outputs optimized for analysis and reasoning.
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import re

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

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
    start_time_ms: int,
    end_time_ms: int
) -> Dict[str, Any]:
    """
    Layer 0: Ultra-compact overview of deployments.
    
    Provides the most condensed view possible - just essential counts and high-level patterns.
    Perfect for initial assessment and determining if deeper investigation is needed.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        
    Returns:
        Dict containing ultra-compact overview with status, summary stats, and key insights
    """
    try:
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "summary": {
                    "total_deployments": 0,
                    "time_range": {
                        "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                        "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        deployments = raw_data["timelineList"]
        
        # Extract key metrics
        total_deployments = len(deployments)
        
        # Parse deployment data
        job_types = set()
        build_statuses = {"SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}
        components = set()
        instances = set()
        patterns = set()
        
        for deployment in deployments:
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
            "summary": {
                "total_deployments": total_deployments,
                "build_status_distribution": build_statuses,
                "success_rate_percentage": round(success_rate, 1),
                "unique_components": len(components),
                "unique_instances": len(instances),
                "unique_job_types": len(job_types),
                "unique_patterns": len(patterns),
                "time_span_hours": time_span_hours,
                "top_job_types": [{"job_type": jt, "count": c} for jt, c in top_job_types],
                "time_range": {
                    "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                    "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            },
            "insights": {
                "deployment_health": "excellent" if success_rate >= 95 else "good" if success_rate >= 80 else "poor" if success_rate >= 50 else "critical",
                "deployment_frequency": "high" if total_deployments > 50 else "medium" if total_deployments > 10 else "low",
                "job_diversity": "high" if len(job_types) > 5 else "medium" if len(job_types) > 2 else "low",
                "infrastructure_spread": "multi-component" if len(components) > 1 else "single-component"
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_overview: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments overview: {str(e)}"
        }

# ============================================================================
# LAYER 1: COMPACT LIST
# ============================================================================

@mcp_server.tool()
async def get_deployments_list(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 20,
    build_status: Optional[str] = None,
    job_type: Optional[str] = None,
    sort_by: str = "timestamp"
) -> Dict[str, Any]:
    """
    Layer 1: Compact list of deployments.
    
    Provides a condensed list with essential details for each deployment.
    Good for scanning and identifying deployments of interest.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        limit: Maximum number of deployments to return
        build_status: Filter by build status ("SUCCESS", "FAILURE")
        job_type: Filter by job type (e.g., "WEB", "CORE", "API")
        sort_by: Sort field ("timestamp", "status", "job_type")
        
    Returns:
        Dict containing compact list of deployments with status and metadata
    """
    try:
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "total_found": 0,
                "returned_count": 0,
                "deployments": []
            }
        
        deployments = raw_data["timelineList"]
        
        # Filter deployments
        filtered_deployments = []
        for deployment in deployments:
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
        
        # Create compact representation
        compact_deployments = []
        for deployment in limited_deployments:
            raw_data_str = deployment.get("rawData", "")
            job_type_parsed, build_status_parsed = _parse_deployment_raw_data(raw_data_str)
            
            compact_deployment = {
                "timestamp": deployment.get("timestamp"),
                "datetime": datetime.fromtimestamp(deployment.get("timestamp", 0) / 1000).isoformat() if deployment.get("timestamp") else None,
                "component": deployment.get("componentName"),
                "instance": deployment.get("instanceName"),
                "pattern": deployment.get("patternName"),
                "job_type": job_type_parsed,
                "build_status": build_status_parsed,
                "anomaly_score": deployment.get("anomalyScore", 0.0),
                "is_incident": deployment.get("isIncident", False),
                "active": deployment.get("active", 0),
                "raw_data_preview": raw_data_str[:100] + "..." if len(raw_data_str) > 100 else raw_data_str
            }
            
            compact_deployments.append(compact_deployment)
        
        return {
            "status": "success",
            "total_found": len(filtered_deployments),
            "returned_count": len(compact_deployments),
            "filters": {
                "build_status": build_status,
                "job_type": job_type,
                "sort_by": sort_by,
                "limit": limit
            },
            "deployments": compact_deployments
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_list: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments list: {str(e)}"
        }

# ============================================================================
# LAYER 2: DETAILED SUMMARY
# ============================================================================

@mcp_server.tool()
async def get_deployments_summary(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 10,
    build_status: Optional[str] = None,
    include_context: bool = True
) -> Dict[str, Any]:
    """
    Layer 2: Detailed summary of deployments.
    
    Provides rich details for each deployment including context, patterns, and analysis.
    Good for understanding the nature and impact of each deployment.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        limit: Maximum number of deployments to return
        build_status: Filter by build status ("SUCCESS", "FAILURE")
        include_context: Whether to include contextual analysis
        
    Returns:
        Dict containing detailed deployment summaries with status and metadata
    """
    try:
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "total_found": 0,
                "returned_count": 0,
                "deployments": []
            }
        
        deployments = raw_data["timelineList"]
        
        # Filter deployments
        if build_status:
            filtered_deployments = []
            for deployment in deployments:
                raw_data_str = deployment.get("rawData", "")
                _, parsed_status = _parse_deployment_raw_data(raw_data_str)
                if parsed_status == build_status:
                    filtered_deployments.append(deployment)
        else:
            filtered_deployments = deployments
        
        # Sort by timestamp (most recent first)
        filtered_deployments.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit results
        limited_deployments = filtered_deployments[:limit]
        
        # Create detailed summaries
        detailed_deployments = []
        for deployment in limited_deployments:
            raw_data_str = deployment.get("rawData", "")
            job_type_parsed, build_status_parsed = _parse_deployment_raw_data(raw_data_str)
            result_info = deployment.get("rootCauseResultInfo", {})
            
            # Parse additional raw data details
            raw_data_details = _parse_detailed_raw_data(raw_data_str)
            
            detailed_deployment = {
                "timestamp": deployment.get("timestamp"),
                "datetime": datetime.fromtimestamp(deployment.get("timestamp", 0) / 1000).isoformat() if deployment.get("timestamp") else None,
                "active": deployment.get("active", 0),
                
                # Location information
                "location": {
                    "component": deployment.get("componentName"),
                    "instance": deployment.get("instanceName")
                },
                
                # Deployment information
                "deployment": {
                    "job_type": job_type_parsed,
                    "build_status": build_status_parsed,
                    "pattern_name": deployment.get("patternName"),
                    "anomaly_score": deployment.get("anomalyScore", 0.0),
                    "raw_data_details": raw_data_details
                },
                
                # System status
                "system_status": {
                    "is_incident": deployment.get("isIncident", False),
                    "active": deployment.get("active", 0)
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
                detailed_deployment["analysis"] = _analyze_deployment(deployment)
            
            detailed_deployments.append(detailed_deployment)
        
        return {
            "status": "success",
            "total_found": len(filtered_deployments),
            "returned_count": len(detailed_deployments),
            "filters": {
                "build_status": build_status,
                "limit": limit,
                "include_context": include_context
            },
            "deployments": detailed_deployments,
            "summary_stats": _calculate_deployment_summary_stats(detailed_deployments) if include_context else None
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_summary: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments summary: {str(e)}"
        }

# ============================================================================
# LAYER 3: FULL DETAILS
# ============================================================================

@mcp_server.tool()
async def get_deployment_details(
    system_name: str,
    deployment_timestamp: int,
    include_analysis: bool = True
) -> Dict[str, Any]:
    """
    Layer 3: Full details for a specific deployment.
    
    Provides complete information about a single deployment including all available
    metadata, analysis, and contextual information.
    
    Args:
        system_name: Name of the system to query
        deployment_timestamp: Timestamp of the specific deployment
        include_analysis: Whether to include detailed analysis
        
    Returns:
        Dict containing complete deployment details with status and metadata
    """
    try:
        client = _get_api_client()
        
        # Use a small time window around the timestamp to find the specific deployment
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = deployment_timestamp - window_ms
        end_time_ms = deployment_timestamp + window_ms
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "error",
                "message": f"No deployment found at timestamp {deployment_timestamp}"
            }
        
        # Find the specific deployment
        target_deployment = None
        for deployment in raw_data["timelineList"]:
            if deployment.get("timestamp") == deployment_timestamp:
                target_deployment = deployment
                break
        
        if not target_deployment:
            return {
                "status": "error",
                "message": f"Specific deployment not found at timestamp {deployment_timestamp}"
            }
        
        # Extract all available information
        raw_data_str = target_deployment.get("rawData", "")
        job_type_parsed, build_status_parsed = _parse_deployment_raw_data(raw_data_str)
        raw_data_details = _parse_detailed_raw_data(raw_data_str)
        result_info = target_deployment.get("rootCauseResultInfo", {})
        
        detailed_info = {
            "status": "success",
            "deployment": {
                # Basic identification
                "timestamp": target_deployment.get("timestamp"),
                "datetime": datetime.fromtimestamp(target_deployment.get("timestamp", 0) / 1000).isoformat() if target_deployment.get("timestamp") else None,
                "active": target_deployment.get("active", 0),
                
                # Location and infrastructure
                "infrastructure": {
                    "component_name": target_deployment.get("componentName"),
                    "instance_name": target_deployment.get("instanceName")
                },
                
                # Deployment details
                "deployment_details": {
                    "job_type": job_type_parsed,
                    "build_status": build_status_parsed,
                    "pattern_name": target_deployment.get("patternName"),
                    "anomaly_score": target_deployment.get("anomalyScore", 0.0),
                    "raw_data_details": raw_data_details,
                    "raw_data_full": raw_data_str
                },
                
                # System state
                "system_state": {
                    "is_incident": target_deployment.get("isIncident", False),
                    "active": target_deployment.get("active", 0)
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
            detailed_info["analysis"] = _comprehensive_deployment_analysis(target_deployment)
        
        # Add raw data availability info
        detailed_info["has_raw_data"] = True  # Deployments always have raw data
        
        return detailed_info
        
    except Exception as e:
        logger.error(f"Error in get_deployment_details: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployment details: {str(e)}"
        }

# ============================================================================
# LAYER 4: RAW DATA
# ============================================================================

@mcp_server.tool()
async def get_deployment_raw_data(
    system_name: str,
    deployment_timestamp: int,
    max_length: int = 10000
) -> Dict[str, Any]:
    """
    Layer 4: Raw data for a specific deployment.
    
    Provides the complete raw JSON data for detailed analysis and debugging.
    Useful for advanced analysis, integration with other tools, or troubleshooting.
    
    Args:
        system_name: Name of the system to query
        deployment_timestamp: Timestamp of the specific deployment
        max_length: Maximum length of raw data to return (for truncation)
        
    Returns:
        Dict containing raw deployment data with status and metadata
    """
    try:
        client = _get_api_client()
        
        # Use a small time window around the timestamp to find the specific deployment
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = deployment_timestamp - window_ms
        end_time_ms = deployment_timestamp + window_ms
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "error",
                "message": f"No deployment found at timestamp {deployment_timestamp}"
            }
        
        # Find the specific deployment
        target_deployment = None
        for deployment in raw_data["timelineList"]:
            if deployment.get("timestamp") == deployment_timestamp:
                target_deployment = deployment
                break
        
        if not target_deployment:
            return {
                "status": "error",
                "message": f"Specific deployment not found at timestamp {deployment_timestamp}"
            }
        
        # Convert to JSON string and check length
        raw_json = json.dumps(target_deployment, indent=2)
        original_length = len(raw_json)
        truncated = False
        
        if original_length > max_length:
            raw_json = raw_json[:max_length] + "\n... [TRUNCATED]"
            truncated = True
        
        # Extract and analyze raw deployment data
        raw_data_str = target_deployment.get("rawData", "")
        job_type, build_status = _parse_deployment_raw_data(raw_data_str)
        
        return {
            "status": "success",
            "deployment_timestamp": deployment_timestamp,
            "raw_data": raw_json,
            "raw_data_length": original_length,
            "returned_length": len(raw_json),
            "truncated": truncated,
            "deployment_info": {
                "job_type": job_type,
                "build_status": build_status,
                "raw_deployment_data": raw_data_str
            },
            "metadata": {
                "data_structure": "InsightFinder deployment timeline entry",
                "contains": [
                    "timestamp and identification",
                    "raw deployment data (job type, build status, etc.)",
                    "anomaly score and system state",
                    "infrastructure information",
                    "root cause result info"
                ],
                "useful_for": [
                    "deployment pipeline analysis",
                    "build failure investigation",
                    "integration with CI/CD tools",
                    "custom deployment analytics"
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployment_raw_data: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployment raw data: {str(e)}"
        }

# ============================================================================
# LAYER 5: STATISTICS
# ============================================================================

@mcp_server.tool()
async def get_deployments_statistics(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    include_trends: bool = True
) -> Dict[str, Any]:
    """
    Layer 5: Comprehensive statistics for deployments.
    
    Provides statistical analysis, trends, and insights across all deployments
    in the time range. Good for understanding patterns and overall deployment health.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        include_trends: Whether to include trend analysis
        
    Returns:
        Dict containing comprehensive statistics with status and metadata
    """
    try:
        client = _get_api_client()
        
        # Fetch raw data
        raw_data = await client.get_deployment(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No deployments found in the specified time range",
                "statistics": {
                    "total_deployments": 0,
                    "time_range": {
                        "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                        "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        deployments = raw_data["timelineList"]
        
        # Basic statistics
        total_deployments = len(deployments)
        
        # Build status analysis
        build_status_counts = {"SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}
        job_type_counts = {}
        component_counts = {}
        instance_counts = {}
        pattern_counts = {}
        
        # Anomaly score analysis
        anomaly_scores = []
        incident_count = 0
        
        for deployment in deployments:
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
                "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
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
                "top_components": get_top_items(component_counts),
                "top_instances": get_top_items(instance_counts),
                "pattern_distribution": get_top_items(pattern_counts)
            },
            
            "anomaly_analysis": {
                "average_score": round(avg_score, 3),
                "max_score": max_score,
                "min_score": min_score,
                "deployments_with_incidents": incident_count,
                "incident_rate_percentage": round(incident_count / total_deployments * 100, 1) if total_deployments > 0 else 0
            }
        }
        
        # Add trend analysis if requested
        if include_trends and total_deployments > 0:
            statistics["trend_analysis"] = _calculate_deployment_trends(deployments, start_time_ms, end_time_ms)
        
        return {
            "status": "success",
            "statistics": statistics,
            "insights": _generate_deployment_insights(statistics)
        }
        
    except Exception as e:
        logger.error(f"Error in get_deployments_statistics: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get deployments statistics: {str(e)}"
        }

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

def _analyze_deployment(deployment: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a single deployment and provide insights."""
    raw_data_str = deployment.get("rawData", "")
    job_type, build_status = _parse_deployment_raw_data(raw_data_str)
    result_info = deployment.get("rootCauseResultInfo", {})
    
    analysis = {
        "deployment_health": "unknown",
        "risk_level": "low",
        "characteristics": [],
        "recommendations": []
    }
    
    # Build status assessment
    if build_status == "SUCCESS":
        analysis["deployment_health"] = "healthy"
        analysis["risk_level"] = "low"
    elif build_status == "FAILURE":
        analysis["deployment_health"] = "failed"
        analysis["risk_level"] = "high"
        analysis["recommendations"].append("investigate build failure logs")
    else:
        analysis["deployment_health"] = "unknown"
        analysis["risk_level"] = "medium"
        analysis["recommendations"].append("verify deployment status")
    
    # Job type specific insights
    if job_type:
        analysis["characteristics"].append(f"{job_type.lower()} deployment")
        if job_type == "WEB":
            analysis["recommendations"].append("monitor web service availability")
        elif job_type == "CORE":
            analysis["recommendations"].append("verify core system functionality")
        elif job_type == "API":
            analysis["recommendations"].append("test API endpoints")
    
    # Context analysis
    if result_info.get("leadToIncident"):
        analysis["characteristics"].append("led to incident")
        analysis["risk_level"] = "critical"
        analysis["recommendations"].append("review incident impact")
    
    if result_info.get("causedByChangeEvent"):
        analysis["characteristics"].append("triggered by change event")
        analysis["recommendations"].append("review change management process")
    
    if deployment.get("isIncident"):
        analysis["characteristics"].append("flagged as incident")
        analysis["risk_level"] = "critical"
    
    return analysis

def _comprehensive_deployment_analysis(deployment: Dict[str, Any]) -> Dict[str, Any]:
    """Provide comprehensive analysis for a deployment."""
    basic_analysis = _analyze_deployment(deployment)
    raw_data_str = deployment.get("rawData", "")
    job_type, build_status = _parse_deployment_raw_data(raw_data_str)
    result_info = deployment.get("rootCauseResultInfo", {})
    
    # Enhanced analysis
    enhanced_analysis = {
        **basic_analysis,
        "deployment_context": {},
        "system_impact": {},
        "timeline_context": {}
    }
    
    # Deployment context
    enhanced_analysis["deployment_context"] = {
        "job_type": job_type,
        "build_status": build_status,
        "pattern_name": deployment.get("patternName"),
        "anomaly_score": deployment.get("anomalyScore", 0.0)
    }
    
    # System impact
    enhanced_analysis["system_impact"] = {
        "is_incident": deployment.get("isIncident", False),
        "active": deployment.get("active", 0),
        "component": deployment.get("componentName"),
        "instance": deployment.get("instanceName")
    }
    
    # Timeline context
    enhanced_analysis["timeline_context"] = {
        "has_related_events": result_info.get("hasPrecedingEvent") or result_info.get("hasTrailingEvent"),
        "part_of_sequence": result_info.get("hasPrecedingEvent") and result_info.get("hasTrailingEvent"),
        "change_driven": result_info.get("causedByChangeEvent"),
        "incident_trigger": result_info.get("leadToIncident")
    }
    
    return enhanced_analysis

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

def _calculate_deployment_trends(deployments: List[Dict[str, Any]], start_time_ms: int, end_time_ms: int) -> Dict[str, Any]:
    """Calculate trend analysis for deployments over time."""
    if not deployments:
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
            "deployment_count": 0,
            "success_count": 0,
            "failure_count": 0
        })
    
    # Distribute deployments into buckets
    for deployment in deployments:
        timestamp = deployment.get("timestamp", 0)
        raw_data_str = deployment.get("rawData", "")
        _, build_status = _parse_deployment_raw_data(raw_data_str)
        
        for bucket in buckets:
            if bucket["start"] <= timestamp < bucket["end"]:
                bucket["deployment_count"] += 1
                if build_status == "SUCCESS":
                    bucket["success_count"] += 1
                elif build_status == "FAILURE":
                    bucket["failure_count"] += 1
                break
    
    # Calculate trend metrics
    deployment_counts = [bucket["deployment_count"] for bucket in buckets]
    success_rates = [
        bucket["success_count"] / bucket["deployment_count"] * 100 
        if bucket["deployment_count"] > 0 else 0 
        for bucket in buckets
    ]
    
    # Simple trend calculation
    deployment_trend = 0
    success_rate_trend = 0
    
    if len(deployment_counts) >= 2:
        deployment_trend = (deployment_counts[-1] - deployment_counts[0]) / max(deployment_counts[0], 1)
        non_zero_rates = [r for r in success_rates if r > 0]
        if len(non_zero_rates) >= 2:
            success_rate_trend = (non_zero_rates[-1] - non_zero_rates[0]) / max(non_zero_rates[0], 1)
    
    return {
        "time_buckets": buckets,
        "trend_indicators": {
            "deployment_frequency_trend": "increasing" if deployment_trend > 0.2 else "decreasing" if deployment_trend < -0.2 else "stable",
            "success_rate_trend": "improving" if success_rate_trend > 0.1 else "declining" if success_rate_trend < -0.1 else "stable",
            "deployment_trend_value": round(deployment_trend, 3),
            "success_rate_trend_value": round(success_rate_trend, 3)
        }
    }

def _generate_deployment_insights(statistics: Dict[str, Any]) -> List[str]:
    """Generate actionable insights from deployment statistics."""
    insights = []
    
    total = statistics.get("total_deployments", 0)
    if total == 0:
        return ["No deployments detected in the specified time range"]
    
    build_analysis = statistics.get("build_analysis", {})
    job_analysis = statistics.get("job_analysis", {})
    infrastructure = statistics.get("infrastructure_analysis", {})
    anomaly_analysis = statistics.get("anomaly_analysis", {})
    
    # Success rate insights
    success_rate = build_analysis.get("success_rate_percentage", 0)
    if success_rate >= 95:
        insights.append(f"Excellent deployment success rate ({success_rate}%) indicates stable CI/CD pipeline")
    elif success_rate >= 80:
        insights.append(f"Good deployment success rate ({success_rate}%) with room for improvement")
    elif success_rate >= 50:
        insights.append(f"Poor deployment success rate ({success_rate}%) requires immediate attention")
    else:
        insights.append(f"Critical deployment success rate ({success_rate}%) indicates serious pipeline issues")
    
    # Failure insights
    failure_count = build_analysis.get("failure_count", 0)
    if failure_count > total * 0.2:
        insights.append(f"High failure rate ({failure_count} failures) suggests build quality issues")
    
    # Job type insights
    unique_jobs = job_analysis.get("unique_job_types", 0)
    if unique_jobs == 1:
        insights.append("Deployments limited to single job type - consider deployment diversity")
    elif unique_jobs > 5:
        insights.append("High job type diversity indicates complex deployment pipeline")
    
    # Infrastructure insights
    unique_components = infrastructure.get("unique_components", 0)
    if unique_components == 1:
        insights.append("Deployments concentrated on single component - potential single point of failure")
    elif unique_components > 10:
        insights.append("Deployments across many components - ensure coordination")
    
    # Incident insights
    incident_rate = anomaly_analysis.get("incident_rate_percentage", 0)
    if incident_rate > 10:
        insights.append(f"High incident rate ({incident_rate}%) from deployments - review deployment process")
    
    # Frequency insights
    duration_hours = statistics.get("time_range", {}).get("duration_hours", 24)
    deployment_frequency = total / duration_hours if duration_hours > 0 else 0
    if deployment_frequency > 2:
        insights.append("High deployment frequency - ensure adequate testing")
    elif deployment_frequency < 0.1:
        insights.append("Low deployment frequency - consider more frequent releases")
    
    return insights
