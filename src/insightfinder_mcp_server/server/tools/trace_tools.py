"""
Multi-layered trace tools for the InsightFinder MCP server.

This module provides a progressive drill-down approach for exploring traces:
- Layer 0: Ultra-compact overview (get_traces_overview)
- Layer 1: Compact list (get_traces_list) 
- Layer 2: Detailed summary (get_traces_summary)
- Layer 3: Full details (get_trace_details)
- Layer 4: Raw data (get_trace_raw_data)
- Layer 5: Statistics (get_traces_statistics)

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
from ...api_client.insightfinder_client import api_client

logger = logging.getLogger(__name__)

# ============================================================================
# LAYER 0: ULTRA-COMPACT OVERVIEW
# ============================================================================

@mcp_server.tool()
async def get_traces_overview(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int
) -> Dict[str, Any]:
    """
    Layer 0: Ultra-compact overview of traces.
    
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
        client = api_client
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No traces found in the specified time range",
                "summary": {
                    "total_traces": 0,
                    "time_range": {
                        "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                        "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        traces = raw_data["timelineList"]
        
        # Extract key metrics
        total_traces = len(traces)
        
        # Parse trace data
        operation_names = set()
        error_states = {"error": 0, "success": 0, "unknown": 0}
        components = set()
        instances = set()
        patterns = set()
        
        # Duration and performance analysis
        durations = []
        error_traces = 0
        
        for trace in traces:
            # Parse raw data
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            
            # Operation names
            operation_name = trace_info.get("operationName")
            if operation_name:
                operation_names.add(operation_name)
            
            # Error analysis
            has_error = trace_info.get("error", False)
            if has_error:
                error_states["error"] += 1
                error_traces += 1
            else:
                error_states["success"] += 1
            
            # Duration analysis
            duration = trace_info.get("duration", 0)
            if duration and isinstance(duration, (int, float)):
                durations.append(duration)
            
            # Infrastructure
            if trace.get("componentName"):
                components.add(trace["componentName"])
            if trace.get("instanceName"):
                instances.add(trace["instanceName"])
            if trace.get("patternName"):
                patterns.add(trace["patternName"])
        
        # Calculate time span
        timestamps = [t.get("timestamp", 0) for t in traces if t.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        # Performance statistics
        if durations:
            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            min_duration = min(durations)
        else:
            avg_duration = max_duration = min_duration = 0
        
        # Error rate
        error_rate = (error_traces / total_traces * 100) if total_traces > 0 else 0
        
        # Top operations
        operation_counts = {}
        for trace in traces:
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            op_name = trace_info.get("operationName")
            if op_name:
                operation_counts[op_name] = operation_counts.get(op_name, 0) + 1
        
        top_operations = sorted(operation_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        return {
            "status": "success",
            "summary": {
                "total_traces": total_traces,
                "error_analysis": {
                    "error_count": error_traces,
                    "success_count": total_traces - error_traces,
                    "error_rate_percentage": round(error_rate, 1)
                },
                "performance_metrics": {
                    "avg_duration_ms": round(avg_duration, 1) if avg_duration else 0,
                    "max_duration_ms": max_duration,
                    "min_duration_ms": min_duration,
                    "traces_with_duration": len(durations)
                },
                "infrastructure": {
                    "unique_components": len(components),
                    "unique_instances": len(instances),
                    "unique_operations": len(operation_names),
                    "unique_patterns": len(patterns)
                },
                "top_operations": [{"operation": op, "count": cnt} for op, cnt in top_operations],
                "time_analysis": {
                    "time_span_hours": time_span_hours,
                    "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                    "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                    "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                }
            },
            "insights": {
                "trace_health": "excellent" if error_rate < 1 else "good" if error_rate < 5 else "poor" if error_rate < 20 else "critical",
                "trace_volume": "high" if total_traces > 1000 else "medium" if total_traces > 100 else "low",
                "performance_status": "fast" if avg_duration < 100 else "moderate" if avg_duration < 1000 else "slow",
                "operation_diversity": "high" if len(operation_names) > 10 else "medium" if len(operation_names) > 3 else "low"
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_traces_overview: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get traces overview: {str(e)}"
        }

# ============================================================================
# LAYER 1: COMPACT LIST
# ============================================================================

@mcp_server.tool()
async def get_traces_list(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 20,
    has_error: Optional[bool] = None,
    operation_name: Optional[str] = None,
    sort_by: str = "timestamp"
) -> Dict[str, Any]:
    """
    Layer 1: Compact list of traces.
    
    Provides a condensed list with essential details for each trace.
    Good for scanning and identifying traces of interest.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        limit: Maximum number of traces to return
        has_error: Filter by error status (True/False/None for all)
        operation_name: Filter by operation name
        sort_by: Sort field ("timestamp", "duration", "error")
        
    Returns:
        Dict containing compact list of traces with status and metadata
    """
    try:
        client = api_client
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No traces found in the specified time range",
                "total_found": 0,
                "returned_count": 0,
                "traces": []
            }
        
        traces = raw_data["timelineList"]
        
        # Filter traces
        filtered_traces = []
        for trace in traces:
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            
            # Apply filters
            if has_error is not None and trace_info.get("error", False) != has_error:
                continue
            if operation_name and trace_info.get("operationName") != operation_name:
                continue
                
            filtered_traces.append(trace)
        
        # Sort traces
        if sort_by == "duration":
            filtered_traces.sort(key=lambda x: _parse_trace_raw_data(x.get("rawData", "")).get("duration", 0), reverse=True)
        elif sort_by == "error":
            filtered_traces.sort(key=lambda x: _parse_trace_raw_data(x.get("rawData", "")).get("error", False), reverse=True)
        else:  # timestamp
            filtered_traces.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit results
        limited_traces = filtered_traces[:limit]
        
        # Create compact representation
        compact_traces = []
        for trace in limited_traces:
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            
            compact_trace = {
                "timestamp": trace.get("timestamp"),
                "datetime": datetime.fromtimestamp(trace.get("timestamp", 0) / 1000).isoformat() if trace.get("timestamp") else None,
                "trace_id": trace_info.get("traceID"),
                "span_id": trace_info.get("spanID"),
                "operation_name": trace_info.get("operationName"),
                "duration_ms": trace_info.get("duration", 0),
                "has_error": trace_info.get("error", False),
                "component": trace.get("componentName"),
                "instance": trace.get("instanceName"),
                "pattern": trace.get("patternName"),
                "anomaly_score": trace.get("anomalyScore", 0.0),
                "is_incident": trace.get("isIncident", False),
                "active": trace.get("active", 0),
                "error_info": _extract_error_info(trace_info) if trace_info.get("error") else None
            }
            
            compact_traces.append(compact_trace)
        
        return {
            "status": "success",
            "total_found": len(filtered_traces),
            "returned_count": len(compact_traces),
            "filters": {
                "has_error": has_error,
                "operation_name": operation_name,
                "sort_by": sort_by,
                "limit": limit
            },
            "traces": compact_traces
        }
        
    except Exception as e:
        logger.error(f"Error in get_traces_list: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get traces list: {str(e)}"
        }

# ============================================================================
# LAYER 2: DETAILED SUMMARY
# ============================================================================

@mcp_server.tool()
async def get_traces_summary(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int = 10,
    has_error: Optional[bool] = None,
    include_context: bool = True
) -> Dict[str, Any]:
    """
    Layer 2: Detailed summary of traces.
    
    Provides rich details for each trace including context, patterns, and analysis.
    Good for understanding the nature and impact of each trace.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        limit: Maximum number of traces to return
        has_error: Filter by error status (True/False/None for all)
        include_context: Whether to include contextual analysis
        
    Returns:
        Dict containing detailed trace summaries with status and metadata
    """
    try:
        client = api_client
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No traces found in the specified time range",
                "total_found": 0,
                "returned_count": 0,
                "traces": []
            }
        
        traces = raw_data["timelineList"]
        
        # Filter traces
        if has_error is not None:
            filtered_traces = []
            for trace in traces:
                trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
                if trace_info.get("error", False) == has_error:
                    filtered_traces.append(trace)
        else:
            filtered_traces = traces
        
        # Sort by timestamp (most recent first)
        filtered_traces.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit results
        limited_traces = filtered_traces[:limit]
        
        # Create detailed summaries
        detailed_traces = []
        for trace in limited_traces:
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            result_info = trace.get("rootCauseResultInfo", {})
            
            detailed_trace = {
                "timestamp": trace.get("timestamp"),
                "datetime": datetime.fromtimestamp(trace.get("timestamp", 0) / 1000).isoformat() if trace.get("timestamp") else None,
                "active": trace.get("active", 0),
                
                # Trace identification
                "trace_identity": {
                    "trace_id": trace_info.get("traceID"),
                    "span_id": trace_info.get("spanID"),
                    "parent_span_id": trace_info.get("parentSpanId"),
                    "operation_name": trace_info.get("operationName")
                },
                
                # Location information
                "location": {
                    "component": trace.get("componentName"),
                    "instance": trace.get("instanceName")
                },
                
                # Trace execution details
                "execution": {
                    "start_time": trace_info.get("startTime"),
                    "trace_time": trace_info.get("traceTime"),
                    "duration_ms": trace_info.get("duration", 0),
                    "has_error": trace_info.get("error", False),
                    "pattern_name": trace.get("patternName"),
                    "anomaly_score": trace.get("anomalyScore", 0.0)
                },
                
                # System status
                "system_status": {
                    "is_incident": trace.get("isIncident", False),
                    "active": trace.get("active", 0)
                },
                
                # Error analysis
                "error_analysis": _analyze_trace_error(trace_info) if trace_info.get("error") else None,
                
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
                detailed_trace["analysis"] = _analyze_trace_performance(trace_info)
            
            detailed_traces.append(detailed_trace)
        
        return {
            "status": "success",
            "total_found": len(filtered_traces),
            "returned_count": len(detailed_traces),
            "filters": {
                "has_error": has_error,
                "limit": limit,
                "include_context": include_context
            },
            "traces": detailed_traces,
            "summary_stats": _calculate_trace_summary_stats(detailed_traces) if include_context else None
        }
        
    except Exception as e:
        logger.error(f"Error in get_traces_summary: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get traces summary: {str(e)}"
        }

# ============================================================================
# LAYER 3: FULL DETAILS
# ============================================================================

@mcp_server.tool()
async def get_trace_details(
    system_name: str,
    trace_timestamp: int,
    include_analysis: bool = True
) -> Dict[str, Any]:
    """
    Layer 3: Full details for a specific trace.
    
    Provides complete information about a single trace including all available
    metadata, analysis, and contextual information.
    
    Args:
        system_name: Name of the system to query
        trace_timestamp: Timestamp of the specific trace
        include_analysis: Whether to include detailed analysis
        
    Returns:
        Dict containing complete trace details with status and metadata
    """
    try:
        client = api_client
        
        # Use a small time window around the timestamp to find the specific trace
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = trace_timestamp - window_ms
        end_time_ms = trace_timestamp + window_ms
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "error",
                "message": f"No trace found at timestamp {trace_timestamp}"
            }
        
        # Find the specific trace
        target_trace = None
        for trace in raw_data["timelineList"]:
            if trace.get("timestamp") == trace_timestamp:
                target_trace = trace
                break
        
        if not target_trace:
            return {
                "status": "error",
                "message": f"Specific trace not found at timestamp {trace_timestamp}"
            }
        
        # Extract all available information
        trace_info = _parse_trace_raw_data(target_trace.get("rawData", ""))
        result_info = target_trace.get("rootCauseResultInfo", {})
        
        detailed_info = {
            "status": "success",
            "trace": {
                # Basic identification
                "timestamp": target_trace.get("timestamp"),
                "datetime": datetime.fromtimestamp(target_trace.get("timestamp", 0) / 1000).isoformat() if target_trace.get("timestamp") else None,
                "active": target_trace.get("active", 0),
                
                # Trace identification and hierarchy
                "identity": {
                    "trace_id": trace_info.get("traceID"),
                    "span_id": trace_info.get("spanID"),
                    "parent_span_id": trace_info.get("parentSpanId"),
                    "parent_span_ids": trace_info.get("parentsSpanIds", []),
                    "operation_name": trace_info.get("operationName"),
                    "is_trace": trace_info.get("isTrace", False)
                },
                
                # Location and infrastructure
                "infrastructure": {
                    "component_name": target_trace.get("componentName"),
                    "instance_name": target_trace.get("instanceName")
                },
                
                # Execution timing and performance
                "execution_details": {
                    "start_time": trace_info.get("startTime"),
                    "trace_time": trace_info.get("traceTime"),
                    "duration_ms": trace_info.get("duration", 0),
                    "has_error": trace_info.get("error", False),
                    "pattern_name": target_trace.get("patternName"),
                    "anomaly_score": target_trace.get("anomalyScore", 0.0)
                },
                
                # Attributes and metadata
                "attributes": trace_info.get("attributes", {}),
                
                # System state
                "system_state": {
                    "is_incident": target_trace.get("isIncident", False),
                    "active": target_trace.get("active", 0)
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
            detailed_info["analysis"] = _comprehensive_trace_analysis(target_trace, trace_info)
        
        # Add raw data availability info
        detailed_info["has_raw_data"] = True  # Traces always have raw data
        
        return detailed_info
        
    except Exception as e:
        logger.error(f"Error in get_trace_details: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get trace details: {str(e)}"
        }

# ============================================================================
# LAYER 4: RAW DATA
# ============================================================================

@mcp_server.tool()
async def get_trace_raw_data(
    system_name: str,
    trace_timestamp: int,
    max_length: int = 10000
) -> Dict[str, Any]:
    """
    Layer 4: Raw data for a specific trace.
    
    Provides the complete raw JSON data for detailed analysis and debugging.
    Useful for advanced analysis, integration with other tools, or troubleshooting.
    
    Args:
        system_name: Name of the system to query
        trace_timestamp: Timestamp of the specific trace
        max_length: Maximum length of raw data to return (for truncation)
        
    Returns:
        Dict containing raw trace data with status and metadata
    """
    try:
        client = api_client
        
        # Use a small time window around the timestamp to find the specific trace
        window_ms = 5 * 60 * 1000  # 5 minutes
        start_time_ms = trace_timestamp - window_ms
        end_time_ms = trace_timestamp + window_ms
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "error",
                "message": f"No trace found at timestamp {trace_timestamp}"
            }
        
        # Find the specific trace
        target_trace = None
        for trace in raw_data["timelineList"]:
            if trace.get("timestamp") == trace_timestamp:
                target_trace = trace
                break
        
        if not target_trace:
            return {
                "status": "error",
                "message": f"Specific trace not found at timestamp {trace_timestamp}"
            }
        
        # Convert to JSON string and check length
        raw_json = json.dumps(target_trace, indent=2)
        original_length = len(raw_json)
        truncated = False
        
        if original_length > max_length:
            raw_json = raw_json[:max_length] + "\n... [TRUNCATED]"
            truncated = True
        
        # Extract and analyze raw trace data
        trace_info = _parse_trace_raw_data(target_trace.get("rawData", ""))
        
        return {
            "status": "success",
            "trace_timestamp": trace_timestamp,
            "raw_data": raw_json,
            "raw_data_length": original_length,
            "returned_length": len(raw_json),
            "truncated": truncated,
            "trace_info": {
                "trace_id": trace_info.get("traceID"),
                "operation_name": trace_info.get("operationName"),
                "duration_ms": trace_info.get("duration", 0),
                "has_error": trace_info.get("error", False),
                "parsed_raw_data": trace_info
            },
            "metadata": {
                "data_structure": "InsightFinder trace timeline entry",
                "contains": [
                    "timestamp and identification",
                    "raw trace data (OpenTelemetry format)",
                    "anomaly score and system state",
                    "infrastructure information",
                    "root cause result info"
                ],
                "useful_for": [
                    "distributed tracing analysis",
                    "performance investigation",
                    "error root cause analysis",
                    "integration with APM tools",
                    "custom trace analytics"
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"Error in get_trace_raw_data: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get trace raw data: {str(e)}"
        }

# ============================================================================
# LAYER 5: STATISTICS
# ============================================================================

@mcp_server.tool()
async def get_traces_statistics(
    system_name: str,
    start_time_ms: int,
    end_time_ms: int,
    include_trends: bool = True
) -> Dict[str, Any]:
    """
    Layer 5: Comprehensive statistics for traces.
    
    Provides statistical analysis, trends, and insights across all traces
    in the time range. Good for understanding patterns and overall system performance.
    
    Args:
        system_name: Name of the system to query
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: End timestamp in milliseconds
        include_trends: Whether to include trend analysis
        
    Returns:
        Dict containing comprehensive statistics with status and metadata
    """
    try:
        client = api_client
        
        # Fetch raw data
        raw_data = await client.get_traces(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms
        )
        
        if not raw_data.get("timelineList"):
            return {
                "status": "success",
                "message": "No traces found in the specified time range",
                "statistics": {
                    "total_traces": 0,
                    "time_range": {
                        "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                        "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                        "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1)
                    }
                }
            }
        
        traces = raw_data["timelineList"]
        
        # Basic statistics
        total_traces = len(traces)
        
        # Performance and error analysis
        operation_counts = {}
        component_counts = {}
        instance_counts = {}
        pattern_counts = {}
        
        # Duration and error tracking
        durations = []
        error_count = 0
        error_operations = {}
        
        # Anomaly score analysis
        anomaly_scores = []
        incident_count = 0
        
        for trace in traces:
            trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
            
            # Operation tracking
            operation = trace_info.get("operationName", "Unknown")
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
            
            # Component tracking
            component = trace.get("componentName", "Unknown")
            component_counts[component] = component_counts.get(component, 0) + 1
            
            # Instance tracking
            instance = trace.get("instanceName", "Unknown")
            instance_counts[instance] = instance_counts.get(instance, 0) + 1
            
            # Pattern tracking
            pattern = trace.get("patternName", "Unknown")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            
            # Duration analysis
            duration = trace_info.get("duration", 0)
            if duration and isinstance(duration, (int, float)):
                durations.append(duration)
            
            # Error analysis
            if trace_info.get("error", False):
                error_count += 1
                error_operations[operation] = error_operations.get(operation, 0) + 1
            
            # Anomaly scores
            score = trace.get("anomalyScore", 0.0)
            anomaly_scores.append(score)
            
            # Incident count
            if trace.get("isIncident"):
                incident_count += 1
        
        # Calculate performance statistics
        if durations:
            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            min_duration = min(durations)
            median_duration = sorted(durations)[len(durations) // 2]
        else:
            avg_duration = max_duration = min_duration = median_duration = 0
        
        # Calculate error rate
        error_rate = (error_count / total_traces * 100) if total_traces > 0 else 0
        
        # Calculate percentages and top items
        def get_top_items(counts_dict, top_n=5):
            sorted_items = sorted(counts_dict.items(), key=lambda x: x[1], reverse=True)
            return {
                item: {"count": count, "percentage": round(count / total_traces * 100, 1)}
                for item, count in sorted_items[:top_n]
            }
        
        # Anomaly score statistics
        if anomaly_scores:
            avg_anomaly_score = sum(anomaly_scores) / len(anomaly_scores)
            max_anomaly_score = max(anomaly_scores)
            min_anomaly_score = min(anomaly_scores)
        else:
            avg_anomaly_score = max_anomaly_score = min_anomaly_score = 0
        
        # Time analysis
        timestamps = [t.get("timestamp", 0) for t in traces if t.get("timestamp")]
        time_span_hours = 0
        if timestamps:
            time_span_hours = round((max(timestamps) - min(timestamps)) / (1000 * 60 * 60), 1)
        
        statistics = {
            "total_traces": total_traces,
            "time_range": {
                "start": datetime.fromtimestamp(start_time_ms / 1000).isoformat(),
                "end": datetime.fromtimestamp(end_time_ms / 1000).isoformat(),
                "duration_hours": round((end_time_ms - start_time_ms) / (1000 * 60 * 60), 1),
                "actual_span_hours": time_span_hours
            },
            
            "performance_analysis": {
                "duration_statistics": {
                    "avg_duration_ms": round(avg_duration, 2),
                    "max_duration_ms": max_duration,
                    "min_duration_ms": min_duration,
                    "median_duration_ms": median_duration,
                    "traces_with_duration": len(durations)
                },
                "error_analysis": {
                    "error_count": error_count,
                    "success_count": total_traces - error_count,
                    "error_rate_percentage": round(error_rate, 1),
                    "top_error_operations": dict(sorted(error_operations.items(), key=lambda x: x[1], reverse=True)[:5])
                }
            },
            
            "operation_analysis": {
                "unique_operations": len(operation_counts),
                "top_operations": get_top_items(operation_counts),
                "operation_distribution": operation_counts
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
                "average_score": round(avg_anomaly_score, 3),
                "max_score": max_anomaly_score,
                "min_score": min_anomaly_score,
                "traces_with_incidents": incident_count,
                "incident_rate_percentage": round(incident_count / total_traces * 100, 1) if total_traces > 0 else 0
            }
        }
        
        # Add trend analysis if requested
        if include_trends and total_traces > 0:
            statistics["trend_analysis"] = _calculate_trace_trends(traces, start_time_ms, end_time_ms)
        
        return {
            "status": "success",
            "statistics": statistics,
            "insights": _generate_trace_insights(statistics)
        }
        
    except Exception as e:
        logger.error(f"Error in get_traces_statistics: {str(e)}")
        return {
            "status": "error",
            "message": f"Failed to get traces statistics: {str(e)}"
        }

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _parse_trace_raw_data(raw_data: str) -> Dict[str, Any]:
    """Parse trace raw data JSON string."""
    if not raw_data:
        return {}
    
    try:
        return json.loads(raw_data)
    except json.JSONDecodeError:
        return {}

def _extract_error_info(trace_info: Dict[str, Any]) -> Dict[str, Any]:
    """Extract error information from trace data."""
    error_info = {}
    
    # Get error details from attributes if available
    attributes = trace_info.get("attributes", {})
    
    if "error" in attributes:
        error_info["error_details"] = attributes["error"]
    
    # Look for HTTP status codes
    if "response.status_code" in attributes:
        error_info["status_code"] = attributes["response.status_code"]
    
    # Look for error messages
    for key in attributes:
        if "error" in key.lower() or "exception" in key.lower():
            error_info[key] = attributes[key]
    
    return error_info

def _analyze_trace_error(trace_info: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze trace error information."""
    analysis = {
        "error_type": "unknown",
        "severity": "medium",
        "characteristics": [],
        "recommendations": []
    }
    
    attributes = trace_info.get("attributes", {})
    
    # Check for HTTP errors
    status_code = attributes.get("response.status_code")
    if status_code:
        try:
            code = int(status_code)
            if 400 <= code < 500:
                analysis["error_type"] = "client_error"
                analysis["severity"] = "medium"
                analysis["characteristics"].append(f"HTTP {code} client error")
                analysis["recommendations"].append("check request parameters and authentication")
            elif 500 <= code < 600:
                analysis["error_type"] = "server_error"
                analysis["severity"] = "high"
                analysis["characteristics"].append(f"HTTP {code} server error")
                analysis["recommendations"].append("investigate server logs and infrastructure")
        except ValueError:
            pass
    
    # Check for specific error patterns
    error_details = attributes.get("error", "")
    if isinstance(error_details, str):
        if "timeout" in error_details.lower():
            analysis["characteristics"].append("timeout error")
            analysis["recommendations"].append("investigate network latency and service response times")
        elif "connection" in error_details.lower():
            analysis["characteristics"].append("connection error")
            analysis["recommendations"].append("check network connectivity and service availability")
    
    # Duration-based analysis
    duration = trace_info.get("duration", 0)
    if duration > 10000:  # More than 10 seconds
        analysis["characteristics"].append("long duration before error")
        analysis["severity"] = "high"
        analysis["recommendations"].append("investigate performance bottlenecks")
    
    return analysis

def _analyze_trace_performance(trace_info: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze trace performance characteristics."""
    analysis = {
        "performance_rating": "unknown",
        "characteristics": [],
        "recommendations": []
    }
    
    duration = trace_info.get("duration", 0)
    operation = trace_info.get("operationName", "")
    
    # Performance rating based on duration
    if duration < 50:
        analysis["performance_rating"] = "excellent"
    elif duration < 200:
        analysis["performance_rating"] = "good"
    elif duration < 1000:
        analysis["performance_rating"] = "acceptable"
    elif duration < 5000:
        analysis["performance_rating"] = "slow"
    else:
        analysis["performance_rating"] = "very_slow"
        analysis["recommendations"].append("investigate performance optimization opportunities")
    
    # Operation-specific insights
    if operation:
        analysis["characteristics"].append(f"{operation} operation")
        if "chat" in operation.lower():
            analysis["recommendations"].append("monitor chat response times and model performance")
    
    # Error correlation
    if trace_info.get("error", False):
        analysis["characteristics"].append("completed with error")
        analysis["recommendations"].append("investigate error root cause")
    
    return analysis

def _comprehensive_trace_analysis(trace: Dict[str, Any], trace_info: Dict[str, Any]) -> Dict[str, Any]:
    """Provide comprehensive analysis for a trace."""
    basic_analysis = _analyze_trace_performance(trace_info)
    result_info = trace.get("rootCauseResultInfo", {})
    
    # Enhanced analysis
    enhanced_analysis = {
        **basic_analysis,
        "trace_context": {},
        "system_impact": {},
        "timeline_context": {}
    }
    
    # Trace context
    enhanced_analysis["trace_context"] = {
        "trace_id": trace_info.get("traceID"),
        "operation_name": trace_info.get("operationName"),
        "duration_ms": trace_info.get("duration", 0),
        "has_error": trace_info.get("error", False),
        "span_hierarchy": {
            "span_id": trace_info.get("spanID"),
            "parent_span_id": trace_info.get("parentSpanId"),
            "has_parent": bool(trace_info.get("parentSpanId"))
        }
    }
    
    # System impact
    enhanced_analysis["system_impact"] = {
        "is_incident": trace.get("isIncident", False),
        "active": trace.get("active", 0),
        "component": trace.get("componentName"),
        "instance": trace.get("instanceName"),
        "anomaly_score": trace.get("anomalyScore", 0.0)
    }
    
    # Timeline context
    enhanced_analysis["timeline_context"] = {
        "has_related_events": result_info.get("hasPrecedingEvent") or result_info.get("hasTrailingEvent"),
        "part_of_sequence": result_info.get("hasPrecedingEvent") and result_info.get("hasTrailingEvent"),
        "change_driven": result_info.get("causedByChangeEvent"),
        "incident_trigger": result_info.get("leadToIncident")
    }
    
    return enhanced_analysis

def _calculate_trace_summary_stats(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics for a list of traces."""
    if not traces:
        return {}
    
    total = len(traces)
    error_count = 0
    durations = []
    incident_count = 0
    
    for trace in traces:
        if trace.get("execution", {}).get("has_error"):
            error_count += 1
        
        duration = trace.get("execution", {}).get("duration_ms", 0)
        if duration:
            durations.append(duration)
        
        if trace.get("system_status", {}).get("is_incident"):
            incident_count += 1
    
    stats = {
        "total_traces": total,
        "error_count": error_count,
        "success_count": total - error_count,
        "incident_count": incident_count,
        "error_rate_percentage": round(error_count / total * 100, 1) if total > 0 else 0
    }
    
    if durations:
        stats["performance"] = {
            "avg_duration_ms": round(sum(durations) / len(durations), 1),
            "max_duration_ms": max(durations),
            "min_duration_ms": min(durations)
        }
    
    return stats

def _calculate_trace_trends(traces: List[Dict[str, Any]], start_time_ms: int, end_time_ms: int) -> Dict[str, Any]:
    """Calculate trend analysis for traces over time."""
    if not traces:
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
            "trace_count": 0,
            "error_count": 0,
            "total_duration": 0,
            "duration_count": 0
        })
    
    # Distribute traces into buckets
    for trace in traces:
        timestamp = trace.get("timestamp", 0)
        trace_info = _parse_trace_raw_data(trace.get("rawData", ""))
        
        for bucket in buckets:
            if bucket["start"] <= timestamp < bucket["end"]:
                bucket["trace_count"] += 1
                if trace_info.get("error", False):
                    bucket["error_count"] += 1
                
                duration = trace_info.get("duration", 0)
                if duration:
                    bucket["total_duration"] += duration
                    bucket["duration_count"] += 1
                break
    
    # Calculate trend metrics
    trace_counts = [bucket["trace_count"] for bucket in buckets]
    error_rates = [
        bucket["error_count"] / bucket["trace_count"] * 100 
        if bucket["trace_count"] > 0 else 0 
        for bucket in buckets
    ]
    avg_durations = [
        bucket["total_duration"] / bucket["duration_count"]
        if bucket["duration_count"] > 0 else 0
        for bucket in buckets
    ]
    
    # Simple trend calculation
    trace_trend = 0
    error_trend = 0
    performance_trend = 0
    
    if len(trace_counts) >= 2:
        trace_trend = (trace_counts[-1] - trace_counts[0]) / max(trace_counts[0], 1)
        
        non_zero_errors = [r for r in error_rates if r > 0]
        if len(non_zero_errors) >= 2:
            error_trend = (non_zero_errors[-1] - non_zero_errors[0]) / max(non_zero_errors[0], 1)
        
        non_zero_durations = [d for d in avg_durations if d > 0]
        if len(non_zero_durations) >= 2:
            performance_trend = (non_zero_durations[-1] - non_zero_durations[0]) / max(non_zero_durations[0], 1)
    
    return {
        "time_buckets": buckets,
        "trend_indicators": {
            "trace_volume_trend": "increasing" if trace_trend > 0.2 else "decreasing" if trace_trend < -0.2 else "stable",
            "error_rate_trend": "increasing" if error_trend > 0.1 else "decreasing" if error_trend < -0.1 else "stable",
            "performance_trend": "degrading" if performance_trend > 0.1 else "improving" if performance_trend < -0.1 else "stable",
            "trace_trend_value": round(trace_trend, 3),
            "error_trend_value": round(error_trend, 3),
            "performance_trend_value": round(performance_trend, 3)
        }
    }

def _generate_trace_insights(statistics: Dict[str, Any]) -> List[str]:
    """Generate actionable insights from trace statistics."""
    insights = []
    
    total = statistics.get("total_traces", 0)
    if total == 0:
        return ["No traces detected in the specified time range"]
    
    performance = statistics.get("performance_analysis", {})
    operation = statistics.get("operation_analysis", {})
    infrastructure = statistics.get("infrastructure_analysis", {})
    anomaly = statistics.get("anomaly_analysis", {})
    
    # Error rate insights
    error_rate = performance.get("error_analysis", {}).get("error_rate_percentage", 0)
    if error_rate >= 10:
        insights.append(f"High error rate ({error_rate}%) indicates system instability")
    elif error_rate >= 5:
        insights.append(f"Moderate error rate ({error_rate}%) requires monitoring")
    elif error_rate < 1:
        insights.append(f"Excellent error rate ({error_rate}%) shows stable system")
    
    # Performance insights
    avg_duration = performance.get("duration_statistics", {}).get("avg_duration_ms", 0)
    if avg_duration > 1000:
        insights.append("High average response time indicates performance issues")
    elif avg_duration < 100:
        insights.append("Excellent response times indicate good system performance")
    
    # Operation diversity insights
    unique_ops = operation.get("unique_operations", 0)
    if unique_ops == 1:
        insights.append("Single operation type - consider monitoring operation diversity")
    elif unique_ops > 20:
        insights.append("High operation diversity indicates complex system interactions")
    
    # Infrastructure insights
    unique_components = infrastructure.get("unique_components", 0)
    if unique_components == 1:
        insights.append("Traces concentrated on single component")
    elif unique_components > 10:
        insights.append("Traces across many components - ensure proper monitoring")
    
    # Incident insights
    incident_rate = anomaly.get("incident_rate_percentage", 0)
    if incident_rate > 5:
        insights.append(f"High incident rate ({incident_rate}%) from traces requires investigation")
    
    # Volume insights
    duration_hours = statistics.get("time_range", {}).get("duration_hours", 24)
    trace_frequency = total / duration_hours if duration_hours > 0 else 0
    if trace_frequency > 100:
        insights.append("High trace volume - ensure adequate monitoring infrastructure")
    elif trace_frequency < 1:
        insights.append("Low trace volume - verify tracing coverage")
    
    return insights
