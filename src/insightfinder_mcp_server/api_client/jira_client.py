"""
JIRA API client for InsightFinder MCP Server.
"""

import logging
from typing import Dict, Any, List, Optional
import urllib.parse
import httpx
from jira import JIRA
from jira.exceptions import JIRAError

logger = logging.getLogger(__name__)


class JiraAPIClient:
    """JIRA API client wrapper for InsightFinder MCP Server."""
    
    def __init__(self, server_url: str, username: str, api_token: str):
        """Initialize JIRA client with credentials.
        
        Args:
            server_url: JIRA server URL (e.g., https://company.atlassian.net)
            username: JIRA username/email
            api_token: JIRA API token
        """
        self.server_url = server_url
        self.username = username
        self.api_token = api_token
        self._jira_client = None
    
    def _get_client(self) -> JIRA:
        """Get or create JIRA client instance."""
        if self._jira_client is None:
            try:
                self._jira_client = JIRA(
                    server=self.server_url,
                    basic_auth=(self.username, self.api_token)
                )
                logger.info(f"Connected to JIRA server: {self.server_url}")
            except JIRAError as e:
                logger.error(f"Failed to connect to JIRA: {e}")
                raise
        return self._jira_client
    
    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get all JIRA projects accessible to the user.
        
        Returns:
            List of project dictionaries with keys: key, name, id, description
        """
        try:
            jira = self._get_client()
            projects = jira.projects()
            
            result = []
            for project in projects:
                result.append({
                    "key": project.key,
                    "name": project.name,
                    "id": project.id,
                    "description": getattr(project, 'description', ''),
                    "project_type": getattr(project, 'projectTypeKey', 'unknown'),
                    "lead": getattr(project, 'lead', {}).get('displayName', '') if hasattr(project, 'lead') else ''
                })
            
            logger.info(f"Retrieved {len(result)} JIRA projects")
            return result
            
        except JIRAError as e:
            logger.error(f"Failed to get JIRA projects: {e}")
            raise
    
    async def get_assignable_users(self, project_key: str, query: str = "") -> List[Dict[str, Any]]:
        """Get assignable users for a specific project.
        
        This method handles GDPR strict mode by using the REST API v3 endpoint instead of v2,
        which doesn't require the deprecated 'username' parameter.
        
        Filters out app accounts, bot accounts, and service accounts to show only human users.
        
        Args:
            project_key: JIRA project key (e.g., 'II')
            query: Optional search query to filter users
            
        Returns:
            List of user dictionaries with keys: accountId, displayName, emailAddress (only human users)
        """
        try:
            jira = self._get_client()
            
            # Try to get assignable users using REST API v3 endpoint
            # This endpoint doesn't use the deprecated 'username' parameter
            url = f"{self.server_url}/rest/api/3/users/search?maxResults=500"
            
            if query:
                # URL encode the query parameter
                url += f"&query={urllib.parse.quote(query)}"
            
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/json"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, auth=(self.username, self.api_token))
                response.raise_for_status()
                
                users_data = response.json()
                result = []

                # print formatted json response for debugging
                # import json
                # print(json.dumps(users_data, indent=2))
                
                if isinstance(users_data, list):
                    for user in users_data:
                        # Filter out app accounts, bot accounts, and service accounts
                        # Keep only human users (accountType == "atlassian" or check for human indicators)
                        account_type = user.get("accountType", "").lower()
                        display_name = user.get("displayName", "").lower()
                        
                        # Skip app and bot accounts
                        skip_keywords = ["app", "bot", "jira", "slack", "trello", "sketch", "github", "statuspage", "service", "widget", "chat", "opsgenie", "reports", "spreadsheet", "outlook", "workplace"]
                        
                        if any(keyword in display_name for keyword in skip_keywords):
                            continue
                        
                        # Skip if accountType indicates it's an app/service
                        if account_type in ["app", "service", "bot"]:
                            continue

                        # Skip inactive users
                        if not user.get("active", True):
                            continue
                        
                        result.append({
                            "accountId": user.get("accountId", ""),
                            "displayName": user.get("displayName", ""),
                            # "emailAddress": user.get("emailAddress", ""),
                            # "active": user.get("active", True)
                        })
                
                logger.info(f"Retrieved {len(result)} assignable users for project {project_key}")
                return result
            
        except Exception as e:
            logger.error(f"Failed to get assignable users for project {project_key}: {e}")
            # Fallback: Try the old method, which may work depending on JIRA configuration
            try:
                jira = self._get_client()
                # Try without the username parameter by using empty query
                users = jira.search_assignable_users_for_projects("", project_key)
                
                result = []
                for user in users:
                    # Filter out app and bot accounts
                    display_name = getattr(user, 'displayName', '').lower()
                    skip_keywords = ["app", "bot", "jira", "slack", "trello", "sketch", "github", "statuspage", "service", "widget", "chat", "opsgenie", "reports", "spreadsheet", "outlook", "workplace"]
                    
                    if any(keyword in display_name for keyword in skip_keywords):
                        continue

                    # Skip inactive users
                    if not getattr(user, 'active', True):
                        continue

                    result.append({
                        "accountId": user.accountId,
                        "displayName": user.displayName,
                        # "emailAddress": getattr(user, 'emailAddress', ''),
                        # "active": getattr(user, 'active', True)
                    })
                
                logger.info(f"Retrieved {len(result)} assignable users for project {project_key} (fallback method)")
                return result
            except Exception as fallback_error:
                logger.error(f"Fallback method also failed: {fallback_error}")
                raise
    
    async def get_fix_versions(self, project_key: str) -> List[Dict[str, Any]]:
        """Get fix versions for a specific project.
        
        Args:
            project_key: JIRA project key (e.g., 'II')
            
        Returns:
            List of version dictionaries with keys: id, name, description, released, archived
        """
        try:
            jira = self._get_client()
            project = jira.project(project_key)
            versions = jira.project_versions(project)
            
            result = []
            for version in versions:
                result.append({
                    "id": version.id,
                    "name": version.name,
                    "description": getattr(version, 'description', ''),
                    "released": getattr(version, 'released', False),
                    "archived": getattr(version, 'archived', False),
                    "releaseDate": getattr(version, 'releaseDate', None)
                })
            
            logger.info(f"Retrieved {len(result)} fix versions for project {project_key}")
            return result
            
        except JIRAError as e:
            logger.error(f"Failed to get fix versions for project {project_key}: {e}")
            raise
    
    async def get_issue_types(self, project_key: str) -> List[Dict[str, Any]]:
        """Get issue types for a specific project.
        
        Args:
            project_key: JIRA project key (e.g., 'II')
            
        Returns:
            List of issue type dictionaries with keys: id, name, description, iconUrl
        """
        try:
            jira = self._get_client()
            project = jira.project(project_key)
            issue_types = project.issueTypes
            
            result = []
            for issue_type in issue_types:
                result.append({
                    "id": issue_type.id,
                    "name": issue_type.name,
                    "description": getattr(issue_type, 'description', ''),
                    "iconUrl": getattr(issue_type, 'iconUrl', ''),
                    "subtask": getattr(issue_type, 'subtask', False)
                })
            
            logger.info(f"Retrieved {len(result)} issue types for project {project_key}")
            return result
            
        except JIRAError as e:
            logger.error(f"Failed to get issue types for project {project_key}: {e}")
            raise
    
    async def create_issue(self, issue_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new JIRA issue.
        
        Args:
            issue_data: Dictionary containing issue fields like:
                - project: {'key': 'PROJECT_KEY'}
                - summary: 'Issue title'
                - description: 'Issue description'
                - issuetype: {'name': 'Task'} or {'id': 'issue_type_id'}
                - assignee: {'accountId': 'user_account_id'}
                - fixVersions: [{'id': 'version_id'}] (optional)
                
        Returns:
            Dictionary with created issue details: key, id, url
        """
        try:
            jira = self._get_client()
            new_issue = jira.create_issue(fields=issue_data)
            
            result = {
                "key": new_issue.key,
                "id": new_issue.id,
                "url": f"{self.server_url}/browse/{new_issue.key}",
                "summary": issue_data.get('summary', ''),
                "status": "Created"
            }
            
            logger.info(f"Created JIRA issue: {new_issue.key}")
            return result
            
        except JIRAError as e:
            logger.error(f"Failed to create JIRA issue: {e}")
            raise


def create_jira_client(server_url: str, username: str, api_token: str) -> JiraAPIClient:
    """Factory function to create a JIRA API client.
    
    Args:
        server_url: JIRA server URL
        username: JIRA username/email
        api_token: JIRA API token
        
    Returns:
        JiraAPIClient instance
    """
    return JiraAPIClient(server_url, username, api_token)


# Global variable to store current JIRA client instance
_current_jira_client: Optional[JiraAPIClient] = None


def set_current_jira_client(client: Optional[JiraAPIClient]) -> None:
    """Set the current JIRA client instance."""
    global _current_jira_client
    _current_jira_client = client


def get_current_jira_client() -> Optional[JiraAPIClient]:
    """Get the current JIRA client instance."""
    return _current_jira_client