import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

_METRIC_CREATION_TYPE = "AWS_Metric"
_LOG_CREATION_TYPE = "AWS_Log"


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
async def verify_cloudwatch_credentials(
    access_key: str,
    secret_key: str,
    region: str,
    data_type: str,
    instance_type: str = "EC2",
) -> Dict[str, Any]:
    """
    Verify AWS CloudWatch credentials against InsightFinder and return available
    resources (instances + metrics for Metric projects, log groups for Log/Alert projects).

    This is Step 1 of the CloudWatch project creation flow. Call this first to confirm
    the credentials are valid and to discover what can be monitored. Then call
    create_cloudwatch_project with selections from the response.

    Args:
        access_key (str): AWS access key ID.
        secret_key (str): AWS secret access key.
        region (str): AWS region, e.g. "us-east-1".
        data_type (str): Type of data to collect — "Metric", "Log", or "Alert".
        instance_type (str): AWS resource type (default "EC2"). Other values: "RDS", "Lambda", etc.

    Returns:
        On success:
            status: "verified"
            data_type: the data_type passed in
            region: the region passed in
            available_instances: list of instance dicts (for Metric projects)
                each entry has: instanceId, displayName, rawInstanceId, componentName, region
            available_metrics: list of metric name strings (for Metric projects)
            available_log_groups: list of log group name strings (for Log/Alert projects)
            instance_count, metric_count, log_group_count: summary counts
            note: guidance on what to do next
        On failure:
            status: "failed"
            message: error description from the API
    """
    if not access_key or not secret_key or not region:
        return {"status": "failed", "message": "access_key, secret_key, and region are required"}

    project_creation_type = _derive_creation_type(data_type)
    try:
        client = _get_api_client()
        raw = await client.verify_cloudwatch_credentials(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            instance_type=instance_type,
            data_type=data_type,
            project_creation_type=project_creation_type,
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}

    if not raw.get("success"):
        return {"status": "failed", "message": raw.get("message", "Key verification failed")}

    info = raw.get("info", {})

    # Normalise instance list for easy consumption in the next step
    raw_instances = info.get("instances", [])
    available_instances = [
        {
            "instanceId": inst.get("instanceId", ""),
            "displayName": inst.get("instanceDisplayName", ""),
            "rawInstanceId": inst.get("rawInstanceId", ""),
            "componentName": inst.get("componentName", ""),
            "region": inst.get("region", region),
        }
        for inst in raw_instances
    ]

    available_metrics = info.get("metrics", [])
    available_log_groups = info.get("logGroups", [])

    return {
        "status": "verified",
        "data_type": data_type,
        "region": region,
        "instance_type": instance_type,
        "available_instances": available_instances,
        "available_metrics": available_metrics,
        "available_log_groups": available_log_groups,
        "instance_count": len(available_instances),
        "metric_count": len(available_metrics),
        "log_group_count": len(available_log_groups),
        "note": (
            "Credentials verified. For a Metric project select instances and metrics, "
            "then call create_cloudwatch_project. For a Log/Alert project select log groups."
        ),
    }


@mcp_server.tool()
async def create_cloudwatch_project(
    access_key: str,
    secret_key: str,
    region: str,
    project_name: str,
    data_type: str,
    selected_instances: Optional[List[Dict[str, str]]] = None,
    selected_metrics: Optional[List[str]] = None,
    selected_log_groups: Optional[List[str]] = None,
    instance_type: str = "EC2",
    system_name: str = "Default System",
    sampling_interval: int = 300,
    collection_interval: int = 5,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a CloudWatch project in InsightFinder (Step 2 of the flow).

    Call verify_cloudwatch_credentials first to confirm keys are valid and to obtain
    the lists of available instances, metrics, and log groups to pass here.

    Args:
        access_key (str): AWS access key ID (same as used in verify step).
        secret_key (str): AWS secret access key (same as used in verify step).
        region (str): AWS region, e.g. "us-east-1".
        project_name (str): Name for the new InsightFinder project.
        data_type (str): "Metric", "Log", or "Alert" — must match the verify step.

        selected_instances (list[dict], optional): Instances to monitor (Metric projects).
            Each dict must have keys: rawInstanceId, componentName, region.
            These come from the available_instances field of verify_cloudwatch_credentials.
        selected_metrics (list[str], optional): Metric names to collect (Metric projects).
            These come from the available_metrics field of verify_cloudwatch_credentials.
        selected_log_groups (list[str], optional): Log group paths to ingest (Log/Alert projects).
            These come from the available_log_groups field of verify_cloudwatch_credentials.

        instance_type (str): AWS resource type (default "EC2").
        system_name (str): InsightFinder system to add the project to (default "Default System").
        sampling_interval (int): Data collection interval in seconds (default 300).
        collection_interval (int): Agent collection interval in minutes (default 5).
        start_time_ms (int, optional): Historical data start time in epoch milliseconds (Log projects).
        end_time_ms (int, optional): Historical data end time in epoch milliseconds (Log projects).

    Returns:
        On success:
            status: "success"
            project_name: the created project name
            system_name, data_type, region
            instance_count, metric_count, log_group_count: what was configured
        On error:
            status: "error"
            message: description from the API (e.g. project already exists)
    """
    if not access_key or not secret_key or not region or not project_name:
        return {"status": "error", "message": "access_key, secret_key, region, and project_name are required"}

    project_creation_type = _derive_creation_type(data_type)

    # Build newInstances in the format the API expects: [{"c": componentName, "i": rawInstanceId, "r": region}]
    new_instances = []
    if selected_instances:
        for inst in selected_instances:
            raw_id = inst.get("rawInstanceId") or inst.get("instanceId", "")
            component = inst.get("componentName", "")
            inst_region = inst.get("region", region)
            if raw_id:
                new_instances.append({"c": component, "i": raw_id, "r": inst_region})

    metrics = selected_metrics or []
    log_groups = selected_log_groups or []

    try:
        client = _get_api_client()
        raw = await client.create_cloudwatch_project(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            project_name=project_name,
            data_type=data_type,
            project_creation_type=project_creation_type,
            instance_type=instance_type,
            new_instances=new_instances,
            metrics=metrics,
            log_groups=log_groups,
            system_name=system_name,
            sampling_interval=sampling_interval,
            collection_interval=collection_interval,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
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
        "region": region,
        "instance_count": len(new_instances),
        "metric_count": len(metrics),
        "log_group_count": len(log_groups),
    }
    if system_assign_warning:
        result["system_assign_warning"] = system_assign_warning
    return result
