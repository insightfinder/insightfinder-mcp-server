import logging
from typing import Dict, Any, List, Optional

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...api_client.jira_client import get_current_jira_client

logger = logging.getLogger(__name__)

@mcp_server.tool()
async def list_jira_projects() -> Dict[str, Any]:
    """List all JIRA projects accessible to the user."""
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        projects = await jira_client.get_projects()
        return {"status": "success", "projects": projects, "count": len(projects)}
    except Exception as e:
        logger.error(f"Failed to list JIRA projects: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def list_jira_assignees(project_key: str, query: str = "") -> Dict[str, Any]:
    """List assignable users for a specific JIRA project.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II')
      query: Optional search query to filter users by name or email
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        assignees = await jira_client.get_assignable_users(project_key, query)
        return {"status": "success", "assignees": assignees, "count": len(assignees), "project_key": project_key}
    except Exception as e:
        logger.error(f"Failed to list JIRA assignees for project {project_key}: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def list_jira_fix_versions(project_key: str) -> Dict[str, Any]:
    """List fix versions for a specific JIRA project.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II')
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        versions = await jira_client.get_fix_versions(project_key)
        return {"status": "success", "fix_versions": versions, "count": len(versions), "project_key": project_key}
    except Exception as e:
        logger.error(f"Failed to list JIRA fix versions for project {project_key}: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def list_jira_issue_types(project_key: str) -> Dict[str, Any]:
    """List issue types for a specific JIRA project.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II')
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        issue_types = await jira_client.get_issue_types(project_key)
        return {"status": "success", "issue_types": issue_types, "count": len(issue_types), "project_key": project_key}
    except Exception as e:
        logger.error(f"Failed to list JIRA issue types for project {project_key}: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def preview_jira_ticket(
    project_key: str,
    assignee_account_id: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    fix_version_id: Optional[str] = None
) -> Dict[str, Any]:
    """Preview a JIRA ticket before creation with all details formatted for user confirmation.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II')
      assignee_account_id: Account ID of the assignee
      summary: Ticket title/summary
      description: Ticket description/body text
      issue_type: Issue type name (default: 'Task')
      fix_version_id: Optional fix version ID
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Get project info
        projects = await jira_client.get_projects()
        project = next((p for p in projects if p["key"] == project_key), None)
        if not project:
            return {"status": "error", "message": f"Project {project_key} not found"}

        # Get assignee info
        assignees = await jira_client.get_assignable_users(project_key)
        assignee = next((a for a in assignees if a["accountId"] == assignee_account_id), None)
        if not assignee:
            return {"status": "error", "message": f"Assignee {assignee_account_id} not found for project {project_key}"}

        # Get issue types
        issue_types = await jira_client.get_issue_types(project_key)
        issue_type_info = next((it for it in issue_types if it["name"].lower() == issue_type.lower()), None)
        if not issue_type_info:
            return {"status": "error", "message": f"Issue type '{issue_type}' not found for project {project_key}"}

        # Get fix version info if provided
        fix_version_info = None
        if fix_version_id:
            versions = await jira_client.get_fix_versions(project_key)
            fix_version_info = next((v for v in versions if v["id"] == fix_version_id), None)
            if not fix_version_info:
                return {"status": "error", "message": f"Fix version {fix_version_id} not found for project {project_key}"}

        # Create preview
        preview = {
            "project": {
                "key": project["key"],
                "name": project["name"],
                "id": project["id"]
            },
            "summary": summary,
            "description": description,
            "issue_type": {
                "id": issue_type_info["id"],
                "name": issue_type_info["name"]
            },
            "assignee": {
                "accountId": assignee["accountId"],
                "displayName": assignee["displayName"],
                "emailAddress": assignee.get("emailAddress", "")
            },
            "fix_version": fix_version_info,
            "formatted_preview": f"""
JIRA Ticket Preview
══════════════════════════════════════════
Project: {project['name']} ({project['key']})
Summary: {summary}
Issue Type: {issue_type_info['name']}
Assignee: {assignee['displayName']} ({assignee.get('emailAddress', '')})
{f"Fix Version: {fix_version_info['name']}" if fix_version_info else "Fix Version: None"}

Description:
{description}

Please confirm to create this JIRA ticket.
            """.strip()
        }

        return {"status": "success", "preview": preview}

    except Exception as e:
        logger.error(f"Failed to preview JIRA ticket: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def create_jira_ticket(
    project_key: str,
    assignee_account_id: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    fix_version_id: Optional[str] = None,
    confirmed: bool = False
) -> Dict[str, Any]:
    """Create a JIRA ticket directly using JIRA API. Use preview_jira_ticket first to show preview and get user confirmation.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II')
      assignee_account_id: Account ID of the assignee
      summary: Ticket title/summary
      description: Ticket description/body text
      issue_type: Issue type name (default: 'Task')
      fix_version_id: Optional fix version ID
      confirmed: Must be True to actually create the ticket (safety check)

    Returns:
      Dictionary containing creation status and created ticket details.
    """
    if not confirmed:
        return {
            "status": "error", 
            "message": "Ticket creation requires confirmation. Please use preview_jira_ticket first to preview the ticket, then call this function with confirmed=True."
        }

    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Prepare issue data
        issue_data = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
            "assignee": {"accountId": assignee_account_id}
        }

        # Add fix version if provided
        if fix_version_id:
            issue_data["fixVersions"] = [{"id": fix_version_id}]

        # Create the ticket
        result = await jira_client.create_issue(issue_data)
        
        logger.info(f"Successfully created JIRA ticket: {result['key']}")
        
        return {
            "status": "success",
            "ticket": result,
            "message": f"JIRA ticket {result['key']} created successfully. URL: {result['url']}"
        }

    except Exception as e:
        logger.error(f"Failed to create JIRA ticket: {e}")
        return {"status": "error", "message": str(e)}


# @mcp_server.tool()
# async def create_jira_ticket_legacy(
#     jiraAssigneeId: str,
#     summary: str,
#     rawData: str,
#     projectName: str = "jira ticket created by mcp server",
#     patternId: int = 0,
#     anomalyScore: int = 0
# ) -> Dict[str, Any]:
#     """Legacy method: Create a Jira ticket in InsightFinder via InsightFinder API. 
#     DEPRECATED: Use create_jira_ticket for direct JIRA integration.
    
#     Parameters:
#       jiraAssigneeId: Jira account (assignee) id.
#       summary: Ticket title / summary line.
#       rawData: Ticket description/body text.
#       projectName: InsightFinder project name context.
#       patternId: Optional pattern identifier (default 0).
#       anomalyScore: Optional anomaly score (default 0).

#     Returns:
#       Dictionary containing creation status and the upstream API response payload.
#     """
#     api_client = get_current_api_client()
#     if not api_client:
#         return {"status": "error", "message": "API client unavailable in current context"}

#     try:
#         result = await api_client.create_jira_ticket(
#             customer_name=api_client.user_name,
#             project_key="II", # Hardcoded project key
#             jira_assignee_id=jiraAssigneeId,
#             jira_reporter_id="5cc457dfd1af7f0e4a55938c", # Hardcoded reporter ID
#             summary=summary,
#             project_name=projectName,
#             pattern_id=patternId,
#             anomaly_score=anomalyScore,
#             raw_data=rawData,
#             jira_issue_fields='{"fixVersions":"10055"}', # Hardcoded fixVersions field
#         )
#         return result
#     except Exception as e:
#         logger.error(f"Legacy Jira ticket creation failed: {e}")
#         return {"status": "error", "message": str(e)}
