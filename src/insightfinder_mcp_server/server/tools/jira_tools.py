import logging
from typing import Dict, Any, List, Optional

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

logger = logging.getLogger(__name__)

JIRA_ASSIGNEES: List[Dict[str, str]] = [
    {"jiraAssigneeId": "634d840cfe5ff37523577e16", "displayName": "Ashvat Bansal"},
    {"jiraAssigneeId": "5ab8531318c3bd2a73ff9152", "displayName": "Banji Zhang"},
    {"jiraAssigneeId": "712020:93fedf1d-f338-46f7-ab8d-5187065886b9", "displayName": "Bin Zhang"},
    {"jiraAssigneeId": "5d059e6311233f0c4ca07aea", "displayName": "Bo Du"},
    {"jiraAssigneeId": "5ab853144373ef2a697c4fd6", "displayName": "Cai Xu"},
    {"jiraAssigneeId": "712020:5c68b9dc-512e-41c1-adf6-c53671e93288", "displayName": "chenqiang"},
    {"jiraAssigneeId": "5cc457dfd1af7f0e4a55938c", "displayName": "InsightFinder Admin"},
    {"jiraAssigneeId": "712020:98aca9a5-4dbf-4da9-abc4-8c2388e73673", "displayName": "Jianqiao Wang"},
    {"jiraAssigneeId": "712020:628093ef-0419-4253-b528-d570e5a19d02", "displayName": "Maoyu Wang"},
    {"jiraAssigneeId": "712020:ef81c8c5-5602-4de0-9bbd-23f487571771", "displayName": "Mustafa M"},
    {"jiraAssigneeId": "62c64a227273faf658f179d6", "displayName": "Pinlong Wu"},
    {"jiraAssigneeId": "712020:edcdf0e2-00ef-4c55-a07f-5f09c20834f0", "displayName": "Rajat Girish Chandak"},
    {"jiraAssigneeId": "6393c6a5fde064eda2f32447", "displayName": "Tianren Zhou"},
    {"jiraAssigneeId": "5c90eba8999a3f2d4cae74f6", "displayName": "Ting"},
    {"jiraAssigneeId": "712020:0fa8bccd-b166-42f7-a4cc-b3994b380390", "displayName": "Yankun Zhao"},
    {"jiraAssigneeId": "712020:fd0aa925-5c37-4494-9076-dd20b4504633", "displayName": "Yuhong Zou"},
    {"jiraAssigneeId": "712020:943aa2a9-3dd1-4f2a-97a3-03b1af77a8b9", "displayName": "Zhixuan Zhou"},
]

@mcp_server.tool()
def list_jira_assignees() -> Dict[str, Any]:
    """List available Jira assignees (displayName and jiraAssigneeId)."""
    return {"status": "success", "assignees": JIRA_ASSIGNEES, "count": len(JIRA_ASSIGNEES)}

@mcp_server.tool()
async def create_jira_ticket(
    jiraAssigneeId: str,
    summary: str,
    rawData: str,
    projectName: str = "jira ticket created by mcp server",
    patternId: int = 0,
    anomalyScore: int = 0
) -> Dict[str, Any]:
    """Create a Jira ticket in InsightFinder. Use this tool to create Jira tickets directly from the MCP server.
    
    Parameters:
      jiraAssigneeId: Jira account (assignee) id.
      summary: Ticket title / summary line.
      rawData: Ticket description/body text.
      projectName: InsightFinder project name context.
      patternId: Optional pattern identifier (default 0).
      anomalyScore: Optional anomaly score (default 0).

    Returns:
      Dictionary containing creation status and the upstream API response payload.
    """
    api_client = get_current_api_client()
    if not api_client:
        return {"status": "error", "message": "API client unavailable in current context"}

    try:
        result = await api_client.create_jira_ticket(
            customer_name=api_client.user_name,
            project_key="II", # Hardcoded project key
            jira_assignee_id=jiraAssigneeId,
            jira_reporter_id="5cc457dfd1af7f0e4a55938c", # Hardcoded reporter ID
            summary=summary,
            project_name=projectName,
            pattern_id=patternId,
            anomaly_score=anomalyScore,
            raw_data=rawData,
            jira_issue_fields='{"fixVersions":"10055"}', # Hardcoded fixVersions field
        )
        return result
    except Exception as e:
        logger.error(f"Jira ticket creation failed: {e}")
        return {"status": "error", "message": str(e)}
