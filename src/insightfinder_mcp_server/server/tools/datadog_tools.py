import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

_METRIC_CREATION_TYPE = "DataDogMetric"
_LOG_CREATION_TYPE = "DataDogLog"


def _get_api_client():
    client = get_current_api_client()
    if not client:
        raise ValueError(
            "InsightFinder API client not available. "
            "Provide X-InsightFinder-License-Key and X-InsightFinder-User-Name headers."
        )
    return client


def _derive_creation_type(data_type: str) -> str:
    return _METRIC_CREATION_TYPE if data_type.lower() == "metric" else _LOG_CREATION_TYPE


@mcp_server.tool()
async def verify_datadog_credentials(
    app_key: str,
    api_key: str,
    data_type: str,
    site: str = "datadoghq.com",
) -> Dict[str, Any]:
    """
    Verify Datadog credentials against InsightFinder and return available resources.

    This is Step 1 of the Datadog project creation flow. Call this first to confirm
    the credentials are valid and discover what can be monitored.

    For Metric projects: returns available components (tag keys from your Datadog account).
    Then call list_datadog_metrics to browse available metrics.
    Finally call create_datadog_project with your selections.

    For Log/Alert projects: returns available fields and tag filter patterns.
    Then call create_datadog_project with your selections.

    Args:
        app_key (str): Datadog application key (starts with "ddapp_").
        api_key (str): Datadog API key.
        data_type (str): Type of data to collect — "Metric", "Log", or "Alert".
        site (str): Datadog site (default "datadoghq.com"; EU: "datadoghq.eu").

    Returns:
        On success (Metric):
            status: "verified"
            data_type: "Metric"
            available_components: list of component/host names (tag keys from Datadog)
            note: guidance on next steps

        On success (Log/Alert):
            status: "verified"
            data_type: "Log"
            available_fields: list of log field names to include (e.g. ["type","message","tags",...])
            available_tag_patterns: list of tag filter regex patterns for component detection
            note: guidance on next steps

        On failure:
            status: "failed"
            message: error from the API
    """
    if not app_key or not api_key:
        return {"status": "failed", "message": "app_key and api_key are required"}

    project_creation_type = _derive_creation_type(data_type)
    try:
        client = _get_api_client()
        raw = await client.verify_datadog_credentials(
            app_key=app_key,
            api_key=api_key,
            site=site,
            data_type=data_type,
            project_creation_type=project_creation_type,
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}

    if not raw.get("success"):
        return {"status": "failed", "message": raw.get("message", "Credential verification failed")}

    if data_type.lower() == "metric":
        tags = raw.get("tags", {})
        available_components = list(tags.keys()) if isinstance(tags, dict) else []
        return {
            "status": "verified",
            "data_type": data_type,
            "site": site,
            "available_components": available_components,
            "component_count": len(available_components),
            "note": (
                "Credentials verified. Call list_datadog_metrics to browse available metrics, "
                "then call create_datadog_project with a component_name and selected_metrics."
            ),
        }
    else:
        return {
            "status": "verified",
            "data_type": data_type,
            "site": site,
            "available_fields": raw.get("fields", []),
            "available_tag_patterns": raw.get("tags", []),
            "note": (
                "Credentials verified. Select fields from available_fields and tag patterns "
                "from available_tag_patterns (used as component_fields), "
                "then call create_datadog_project."
            ),
        }


@mcp_server.tool()
async def list_datadog_metrics(
    api_key: str,
    app_key: str,
    site: str = "datadoghq.com",
    page: int = 1,
    page_size: int = 100,
    keyword_search: str = "",
    tz_offset: int = -14400000,
) -> Dict[str, Any]:
    """
    Fetch a paginated list of available Datadog metrics for the given credentials.

    Use this after verify_datadog_credentials (Metric flow) to browse and select metrics
    before calling create_datadog_project.

    Args:
        api_key (str): Datadog API key.
        app_key (str): Datadog application key.
        site (str): Datadog site (default "datadoghq.com").
        page (int): Page number starting at 1 (default 1).
        page_size (int): Number of metrics per page, max 100 (default 100).
        keyword_search (str): Optional filter keyword to narrow the metric list.
        tz_offset (int): Timezone offset in milliseconds (default -14400000 = UTC-4).

    Returns:
        On success:
            status: "success"
            metrics: list of metric name strings for this page
            total: total number of available metrics
            page, page_size: pagination info
            has_more: True if additional pages exist
        On failure:
            status: "error"
            message: error description
    """
    if not api_key or not app_key:
        return {"status": "error", "message": "api_key and app_key are required"}

    page_size = min(page_size, 100)

    try:
        client = _get_api_client()
        raw = await client.list_datadog_metrics(
            api_key=api_key,
            app_key=app_key,
            site=site,
            page=page,
            page_size=page_size,
            keyword_search=keyword_search,
            tz_offset=tz_offset,
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}

    if not raw.get("success"):
        return {"status": "error", "message": raw.get("message", "Failed to fetch metric list")}

    metrics = raw.get("results", {}).get("metrics", [])
    total = raw.get("total", 0)

    return {
        "status": "success",
        "metrics": metrics,
        "total": total,
        "page": raw.get("page", page),
        "page_size": raw.get("pageSize", page_size),
        "has_more": (page * page_size) < total,
    }


@mcp_server.tool()
async def create_datadog_project(
    app_key: str,
    api_key: str,
    project_name: str,
    data_type: str,
    site: str = "datadoghq.com",
    system_name: str = "Default System",
    sampling_interval: int = 300,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    # Metric project params
    component_name: Optional[str] = None,
    tags_expression: Optional[str] = None,
    selected_metrics: Optional[List[str]] = None,
    # Log/Alert project params
    selected_fields: Optional[List[str]] = None,
    component_fields: Optional[List[str]] = None,
    instance_key: Optional[str] = None,
    additional_query: Optional[str] = None,
    additional_filter: Optional[List[List[str]]] = None,
    use_host_name: bool = False,
) -> Dict[str, Any]:
    """
    Create a Datadog project in InsightFinder (Step 3 of the flow for Metric, Step 2 for Log).

    For Metric projects — prerequisite steps:
        1. verify_datadog_credentials  → get available_components
        2. list_datadog_metrics        → get metric names to monitor
        3. create_datadog_project      → create the project

    For Log/Alert projects — prerequisite steps:
        1. verify_datadog_credentials  → get available_fields and available_tag_patterns
        2. create_datadog_project      → create the project

    Args:
        app_key (str): Datadog application key.
        api_key (str): Datadog API key.
        project_name (str): Name for the new InsightFinder project.
        data_type (str): "Metric", "Log", or "Alert".
        site (str): Datadog site (default "datadoghq.com").
        system_name (str): InsightFinder system to add the project to (default "Default System").
        sampling_interval (int): Data collection interval in seconds (default 300).
        start_time_ms (int, optional): Historical data window start in epoch milliseconds.
        end_time_ms (int, optional): Historical data window end in epoch milliseconds.

        --- Metric project params ---
        component_name (str): Component/host to monitor, from available_components in verify response.
            Example: "maoyu-workspace"
        tags_expression (str): Datadog tag filter expression.
            Example: "maoyu-workspace" or "env:prod AND service:api"
        selected_metrics (list[str]): Metric names to collect, from list_datadog_metrics.

        --- Log/Alert project params ---
        selected_fields (list[str]): Log fields to include, from available_fields in verify response.
            Example: ["type", "message", "tags", "status", "service", "attributes"]
        component_fields (list[str]): Tag regex patterns for component detection,
            from available_tag_patterns in verify response.
            Example: ["tags=maoyu-workspace:(.*?),"]
        instance_key (str): Template for instance name extraction from log fields.
            Example: "{key} / abx = {xyz}"
        additional_query (str): Optional extra Datadog log query filter.
        additional_filter (list[list[str]]): Optional additional field filters as pairs.
            Example: [["xse", "message=unknown"]]
        use_host_name (bool): Whether to use hostname as instance identifier (default False).

    Returns:
        On success:
            status: "success"
            project_name, system_name, data_type, site
            metric_count (Metric), field_count / log_group_count (Log)
        On error:
            status: "error"
            message: description (e.g. project already exists)
    """
    if not app_key or not api_key or not project_name:
        return {"status": "error", "message": "app_key, api_key, and project_name are required"}

    project_creation_type = _derive_creation_type(data_type)

    try:
        client = _get_api_client()
        raw = await client.create_datadog_project(
            app_key=app_key,
            api_key=api_key,
            site=site,
            project_name=project_name,
            data_type=data_type,
            project_creation_type=project_creation_type,
            system_name=system_name,
            sampling_interval=sampling_interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            component_name=component_name or "",
            tags_expression=tags_expression or "",
            selected_metrics=selected_metrics,
            selected_fields=selected_fields,
            component_fields=component_fields,
            instance_key=instance_key or "",
            additional_query=additional_query or "",
            additional_filter=additional_filter,
            use_host_name=use_host_name,
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}

    if not raw.get("success"):
        return {"status": "error", "message": raw.get("message", "Project creation failed")}

    # Resolve system display name → internal hash, then assign project to system
    system_assigned = False
    system_assign_warning = None
    system_key = await client.resolve_system_key(system_name)
    if system_key:
        assign_result = await client.add_project_to_system(
            project_name=project_name,
            customer_name=client.user_name,
            system_key=system_key,
        )
        system_assigned = assign_result.get("success", False)
        if not system_assigned:
            system_assign_warning = assign_result.get("message", "Failed to assign project to system")
    else:
        system_assign_warning = f"System '{system_name}' not found — project created but not assigned to a system"

    result: Dict[str, Any] = {
        "status": "success",
        "project_name": project_name,
        "system_name": system_name,
        "system_assigned": system_assigned,
        "data_type": data_type,
        "site": site,
    }
    if data_type.lower() == "metric":
        result["component_name"] = component_name or ""
        result["metric_count"] = len(selected_metrics or [])
    else:
        result["field_count"] = len(selected_fields or [])
        result["component_field_count"] = len(component_fields or [])
    if system_assign_warning:
        result["system_assign_warning"] = system_assign_warning
    return result
