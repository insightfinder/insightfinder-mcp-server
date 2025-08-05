import time
import sys
from typing import Dict, Any, Optional, List

from ..server import mcp_server
from ...api_client.insightfinder_client import api_client
from ...config.settings import settings

@mcp_server.tool()
async def fetch_incidents(
    zoneName: Optional[str] = None,
    zoneList: Optional[List[str]] = None,
    startTime: Optional[int] = None,
    endTime: Optional[int] = None,
    environmentName: str = "All",
    includePastOccurrence: bool = True,
) -> Dict[str, Any]:
    """
    Fetches incident timeline data from InsightFinder for a specific system within a given time range.
    Use this tool when a user asks for incidents, issues, problems, or root cause analysis related to a system.

    Args:
        zoneName (str): Optional. The zone name for the system. If not provided, defaults to systemName.
        zoneList (List[str]): Optional. List of zones to query. Defaults to empty list.
        startTime (int): Optional. The start of the time window in Unix timestamp (milliseconds).
                         If not provided, defaults to 1 hour ago.
        endTime (int): Optional. The end of the time window in Unix timestamp (milliseconds).
                       If not provided, defaults to the current time.
        environmentName (str): Optional. The environment name to filter by. Defaults to 'All'.
        includePastOccurrence (bool): Optional. Whether to include past occurrences. Defaults to True.
    """
    try:
        # Set default time range if not provided
        current_time_ms = int(time.time() * 1000)
        if endTime is None:
            endTime = current_time_ms
        if startTime is None:
            startTime = endTime - (1 * 60 * 60 * 1000)  # 1 hour ago
        
        # Set default zoneList if not provided
        if zoneList is None:
            zoneList = []

        # Log the call for debugging (prints to stderr)
        # print(f"Tool 'fetch_incidents' called for system: '{systemName}', customer: '{customerName}', subdomain: '{subdomain}' from {startTime} to {endTime}", file=sys.stderr)


        # Call the InsightFinder API client with the rootcausetimelinesJWT endpoint
        result = await api_client.get_root_cause_timelines(
            zone_name=zoneName,
            zone_list=zoneList,
            start_time_ms=startTime,
            end_time_ms=endTime,
            environment_name=environmentName,
            include_past_occurrence=includePastOccurrence,
        )

        # print(f"Tool 'fetch_incidents' completed for system: '{systemName}', customer: '{customerName}'", file=sys.stderr)
        return result
        
    except Exception as e:
        error_message = f"Error in fetch_incidents: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}