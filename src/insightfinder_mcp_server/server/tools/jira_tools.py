import logging
from typing import Dict, Any, List, Optional
import hashlib
import time

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from ...api_client.jira_client import get_current_jira_client

logger = logging.getLogger(__name__)


def generate_confirmation_code(project_key: str, assignee_account_id: str) -> str:
    """Generate a time-based confirmation code valid for 15 minutes.
    
    The code is independently verifiable without storage:
    - Based on: project_key, assignee_account_id, summary, description, and current 15-min time window
    - Format: 6 uppercase letters
    
    Args:
        project_key: JIRA project key
        assignee_account_id: Account ID of assignee
    
    Returns:
        6-character uppercase confirmation code
    """
    # Get current time window (15-minute intervals, in seconds)
    current_window = int(time.time() // 900)  # 900 seconds = 15 minutes
    
    # Create a deterministic hash from the parameters and time window
    data = f"{project_key}|{assignee_account_id}|{current_window}"
    hash_obj = hashlib.sha256(data.encode())
    hash_hex = hash_obj.hexdigest()
    
    # Convert hex to uppercase letters only (A-Z)
    # Take first 6 characters and map hex digits to letters
    code = ""
    for i in range(6):
        hex_char = hash_hex[i]
        # Map 0-9, a-f to A-P (16 letters, cycling through A-Z)
        char_value = int(hex_char, 16)
        code += chr(65 + (char_value % 26))  # 65 = 'A'
    
    return code


def verify_confirmation_code(project_key: str, assignee_account_id: str, provided_code: str) -> bool:
    """Verify a confirmation code.
    
    Checks against current and previous 15-minute window to account for edge cases.
    Valid for 15 minutes from generation.
    
    Args:
        project_key: JIRA project key
        assignee_account_id: Account ID of assignee
        provided_code: The confirmation code to verify
    
    Returns:
        True if code is valid, False otherwise
    """
    # Check current time window
    current_window = int(time.time() // 900)
    
    # Generate codes for current and previous window (to account for edge cases)
    for window_offset in [0, -1]:
        window = current_window + window_offset
        
        # Recreate the hash with the window
        data = f"{project_key}|{assignee_account_id}|{window}"
        hash_obj = hashlib.sha256(data.encode())
        hash_hex = hash_obj.hexdigest()
        
        # Generate code the same way
        code = ""
        for i in range(6):
            hex_char = hash_hex[i]
            code += chr(65 + (int(hex_char, 16) % 26))
        
        if code.upper() == provided_code.upper():
            logger.info(f"Confirmation code verified successfully")
            return True
    
    logger.warning(f"Invalid or expired confirmation code provided")
    return False



async def resolve_project_key(project_identifier: str) -> str:
    """Resolve project key from either project key or project name.
    
    Args:
        project_identifier: Either a project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
    
    Returns:
        The project key
    
    Raises:
        ValueError: If project not found
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        raise ValueError("JIRA client not available")
    
    projects = await jira_client.get_projects()
    
    # First try to find by key (case-insensitive)
    for project in projects:
        if project["key"].lower() == project_identifier.lower():
            return project["key"]
    
    # Then try to find by name (case-insensitive)
    for project in projects:
        if project["name"].lower() == project_identifier.lower():
            return project["key"]
    
    raise ValueError(f"Project '{project_identifier}' not found. Provide either a project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')")


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
      project_key: JIRA project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
      query: Optional search query to filter users by name or email
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Resolve project key from either key or name
        resolved_project_key = await resolve_project_key(project_key)
        
        assignees = await jira_client.get_assignable_users(resolved_project_key, query)
        return {"status": "success", "assignees": assignees, "count": len(assignees), "project_key": resolved_project_key}
    except ValueError as e:
        logger.error(f"Failed to resolve project: {e}")
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Failed to list JIRA assignees for project {project_key}: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def list_jira_fix_versions(project_key: str) -> Dict[str, Any]:
    """List fix versions for a specific JIRA project.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Resolve project key from either key or name
        resolved_project_key = await resolve_project_key(project_key)
        
        versions = await jira_client.get_fix_versions(resolved_project_key)
        return {"status": "success", "fix_versions": versions, "count": len(versions), "project_key": resolved_project_key}
    except ValueError as e:
        logger.error(f"Failed to resolve project: {e}")
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Failed to list JIRA fix versions for project {project_key}: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def list_jira_issue_types(project_key: str) -> Dict[str, Any]:
    """List issue types for a specific JIRA project.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Resolve project key from either key or name
        resolved_project_key = await resolve_project_key(project_key)
        
        issue_types = await jira_client.get_issue_types(resolved_project_key)
        return {"status": "success", "issue_types": issue_types, "count": len(issue_types), "project_key": resolved_project_key}
    except ValueError as e:
        logger.error(f"Failed to resolve project: {e}")
        return {"status": "error", "message": str(e)}
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
    
    IMPORTANT: This function generates a time-based confirmation code (valid for 15 minutes).
    
    Parameters:
      project_key: JIRA project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
      assignee_account_id: Account ID of the assignee
      summary: Ticket title/summary
      description: Ticket description/body text
      issue_type: Issue type name (default: 'Task')
      fix_version_id: Optional fix version ID
    
    Returns:
      Dictionary containing ticket preview and a confirmation code for create_jira_ticket
    """
    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Resolve project key from either key or name
        resolved_project_key = await resolve_project_key(project_key)
        
        # Get project info
        projects = await jira_client.get_projects()
        project = next((p for p in projects if p["key"] == resolved_project_key), None)
        if not project:
            return {"status": "error", "message": f"Project {project_key} not found"}

        # Get assignee info
        assignees = await jira_client.get_assignable_users(resolved_project_key)
        assignee = next((a for a in assignees if a["accountId"] == assignee_account_id), None)
        if not assignee:
            return {"status": "error", "message": f"Assignee {assignee_account_id} not found for project {project_key}"}

        # Get issue types
        issue_types = await jira_client.get_issue_types(resolved_project_key)
        issue_type_info = next((it for it in issue_types if it["name"].lower() == issue_type.lower()), None)
        if not issue_type_info:
            return {"status": "error", "message": f"Issue type '{issue_type}' not found for project {project_key}"}

        # Get fix version info if provided
        fix_version_info = None
        if fix_version_id:
            versions = await jira_client.get_fix_versions(resolved_project_key)
            fix_version_info = next((v for v in versions if v["id"] == fix_version_id), None)
            if not fix_version_info:
                return {"status": "error", "message": f"Fix version {fix_version_id} not found for project {project_key}"}

        # Generate confirmation code
        confirmation_code = generate_confirmation_code(
            resolved_project_key,
            assignee_account_id
        )

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
            "confirmation_code": confirmation_code,
            "code_validity_minutes": 15,
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

Please confirm by replying Yes or Confirm to create this JIRA ticket. (CONFIRMATION CODE: {confirmation_code})
            """.strip()
        }

        return {"status": "success", "preview": preview}

    except ValueError as e:
        logger.error(f"Failed to resolve project: {e}")
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Failed to preview JIRA ticket: {e}")
        return {"status": "error", "message": str(e)}


@mcp_server.tool()
async def create_jira_ticket(
    project_key: str,
    assignee_account_id: str,
    summary: str,
    description: str,
    confirmation_code: str,
    issue_type: str = "Task",
    fix_version_id: Optional[str] = None
) -> Dict[str, Any]:
    """Create a JIRA ticket directly using JIRA API. 
    
    ⚠️ CRITICAL: ALWAYS preview and get user confirmation before creating a ticket.
    
    MANDATORY WORKFLOW:
    1. FIRST call preview_jira_ticket() with the same parameters to display the ticket preview to the user
    2. Display the formatted preview and CONFIRMATION CODE to the user
    3. Request explicit confirmation from the user
    4. ONLY after user confirms, call this function with the confirmation_code from preview
    
    Do NOT create the ticket directly, even if the user requests it in a single prompt. 
    The preview step is mandatory and non-negotiable.
    
    Parameters:
      project_key: JIRA project key (e.g., 'II') or project name (e.g., 'InsightFinder Infrastructure')
      assignee_account_id: Account ID of the assignee
      summary: Ticket title/summary
      description: Ticket description/body text
      confirmation_code: The 6-character confirmation code from preview_jira_ticket (valid for 15 minutes)
      issue_type: Issue type name (default: 'Task')
      fix_version_id: Optional fix version ID

    Returns:
      Dictionary containing creation status and created ticket details.
    """
    # Validate confirmation code
    if not confirmation_code or len(confirmation_code) != 6:
        return {
            "status": "error", 
            "message": f"Invalid confirmation code format. Expected 6-character code, got '{confirmation_code}'. Please use preview_jira_ticket first."
        }
    
    try:
        # Resolve project key from either key or name FIRST
        # This is critical for confirmation code verification to work correctly
        resolved_project_key = await resolve_project_key(project_key)
    except ValueError as e:
        return {
            "status": "error", 
            "message": f"Failed to resolve project: {str(e)}"
        }
    
    # Verify the confirmation code using the RESOLVED project key
    is_valid = verify_confirmation_code(
        resolved_project_key,
        assignee_account_id,
        confirmation_code
    )
    
    if not is_valid:
        return {
            "status": "error", 
            "message": "Invalid or expired confirmation code. Please call preview_jira_ticket again to get a fresh confirmation code (valid for 15 minutes)."
        }

    jira_client = get_current_jira_client()
    if not jira_client:
        return {"status": "error", "message": "JIRA client not available. Please ensure JIRA credentials are provided in headers."}

    try:
        # Prepare issue data
        issue_data = {
            "project": {"key": resolved_project_key},
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

    except ValueError as e:
        logger.error(f"Failed to resolve project: {e}")
        return {"status": "error", "message": str(e)}
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
