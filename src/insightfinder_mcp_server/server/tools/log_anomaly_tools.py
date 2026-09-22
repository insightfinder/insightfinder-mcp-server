# New Tool to Fetch All Log and Metric Projects

@mcp_server.tool()
async def fetch_all_log_and_metric_projects(system_name: str, start_time: Optional[Union[str, int]] = None, end_time: Optional[Union[str, int]] = None) -> Dict[str, Any]:
    """
    Fetches all log and metric related projects with their anomalies.
    This tool aggregates log anomalies and metric data in the specified time range.
    It incorporates both log and metric retrieval processes to provide a comprehensive overview.
    
    Args:
        system_name (str): The name of the system to query for projects.
        start_time (Optional[Union[str, int]]): The start of the time window for fetching anomalies.
            Accepts timestamps in various formats (e.g., "2026-02-12T11:05:00").
        end_time (Optional[Union[str, int]]): The end of the time window.
    
    Returns:
        A detailed summary of log and metric projects and their anomalies.
""" 
        system_name (str): The name of the system to query for projects.
        start_time (Optional[Union[str, int]]): The start of the time window for fetching anomalies.
            Accepts timestamps in various formats (e.g., "2026-02-12T11:05:00").
        end_time (Optional[Union[str, int]]): The end of the time window.
    
    Returns:
        A detailed summary of log and metric projects and their anomalies.
    """ 
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Validate timestamps
        if start_time_ms is None:
            default_start_ms, _ = get_time_range_ms(tz_name, 1)
            start_time_ms = default_start_ms

        # Call the API to fetch log anomalies
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]

        metrics_project_data = await fetch_metrics_data(system_name, start_time_ms, end_time_ms)
        aggregated_data = aggregate_log_and_metric_data(log_anomalies, metrics_project_data)

        return {
            "status": "success",
            "aggregated_data": aggregated_data
        }

    except Exception as e:
        error_message = f"Error in fetch_all_log_and_metric_projects: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

async def fetch_metrics_data(system_name: str, start_time: int, end_time: int) -> List[Dict[str, Any]]:
    # Here we fetch any relevant metric data for the provided system per specified time range.
    # This function implementation will depend on the specific APIs available.
    pass

def aggregate_log_and_metric_data(log_anomalies: List[Dict[str, Any]], metrics_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Implementation of aggregation logic for log and metrics data.
    pass
