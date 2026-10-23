"""
System information tools for the InsightFinder MCP server.

This module provides tools for querying and exploring InsightFinder systems and projects:
- list_all_systems: The signed-in user's systems (or, with scope="all_visible", every system
  their role can see), with owner and type
- list_all_systems_and_projects: The same systems with their projects
- get_projects_for_system: Get all projects within a specific system
- find_system_by_name: Fuzzy search for system names (handles typos and case differences)

These tools help users discover and navigate the InsightFinder system hierarchy.
"""

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Annotated, Dict, Any, List, Literal, Optional, Union
from difflib import SequenceMatcher

import httpx
from pydantic import Field

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from .ari_report import render_ari_digest
from .get_time import (
    format_timestamp_in_user_timezone,
    get_time_range_ms,
    resolve_system_timezone,
    parse_time_parameters,
)

logger = logging.getLogger(__name__)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_system_json(system_json_str: str) -> Optional[Dict[str, Any]]:
    """
    Parse a system JSON string from the systemframework API.
    Handles nested JSON-in-JSON encoding.
    
    Args:
        system_json_str: JSON string containing system data
        
    Returns:
        Parsed system dictionary or None if parsing fails
    """
    try:
        system = json.loads(system_json_str)
        
        # Parse nested projectDetailsList if present
        if 'projectDetailsList' in system and isinstance(system['projectDetailsList'], str):
            try:
                system['projectDetailsList'] = json.loads(system['projectDetailsList'])
            except (json.JSONDecodeError, TypeError):
                system['projectDetailsList'] = []
        
        return system
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse system JSON: {e}")
        return None


def extract_system_info(system_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract key system information from parsed system data.
    
    Args:
        system_data: Parsed system dictionary
        
    Returns:
        Simplified system info dictionary
    """
    system_key = system_data.get('systemKey', {})
    
    return {
        'systemName': system_key.get('systemName', 'Unknown'),
        'systemDisplayName': system_data.get('systemDisplayName', 'Unknown'),
        'userName': system_key.get('userName', 'Unknown'),
        'environmentName': system_key.get('environmentName', 'All'),
        'timezone': system_data.get('timezone', 'US/Eastern'),
        'projectCount': len(system_data.get('projectDetailsList', [])),
        'isShared': False  # Will be set by caller based on which array it came from
    }


def extract_project_info(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract key project information from parsed project data.
    
    Args:
        project_data: Parsed project dictionary
        
    Returns:
        Simplified project info dictionary
    """
    return {
        'projectName': project_data.get('projectName', 'Unknown'),
        'projectKey': project_data.get('projectKey', 'Unknown'),
        'userName': project_data.get('userName', 'Unknown'),
        'dataType': project_data.get('dataType', 'Unknown'),
        'projectClassType': project_data.get('projectClassType', 'CUSTOM')
    }


def calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity ratio between two strings (case-insensitive).
    
    Args:
        str1: First string
        str2: Second string
        
    Returns:
        Similarity ratio between 0.0 and 1.0
    """
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def find_best_match(target_name: str, available_systems: List[Dict[str, Any]], threshold: float = 0.6) -> Optional[Dict[str, Any]]:
    """
    Find the best matching system name using fuzzy matching.
    
    Args:
        target_name: The system name to search for
        available_systems: List of available system info dictionaries
        threshold: Minimum similarity ratio to consider a match (0.0 to 1.0)
        
    Returns:
        Best matching system info or None if no good match found
    """
    best_match = None
    best_score = 0.0
    
    for system in available_systems:
        display_name = system.get('systemDisplayName', '')
        system_name = system.get('systemName', '')
        
        # Check similarity with display name (primary)
        score_display = calculate_similarity(target_name, display_name)
        # Check similarity with system name (secondary)
        score_system = calculate_similarity(target_name, system_name)
        
        # Use the better score
        score = max(score_display, score_system)
        
        if score > best_score and score >= threshold:
            best_score = score
            best_match = {
                **system,
                'matchScore': score,
                'matchedField': 'systemDisplayName' if score_display > score_system else 'systemName'
            }
    
    return best_match


# ============================================================================
# SYSTEM SCOPE
# ============================================================================

IT_SYSTEM, AI_MODEL = "IT System", "AI Model"
SCOPE_MINE, SCOPE_ALL_VISIBLE = "mine", "all_visible"
_SCOPES = (SCOPE_MINE, SCOPE_ALL_VISIBLE)


def _system_key(system_data: Dict[str, Any]) -> Dict[str, Any]:
    """systemKey of a systemframework entry; it arrives as an object or as a JSON string."""
    raw = system_data.get('systemKey') or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


async def _scoped_entries(api_client, scope: str, include_shared: bool = True) -> Dict[str, Any]:
    """Parsed systemframework entries for a scope: {"status": "success", "entries": [(system_data,
    key, shared)], "unparsedCount"} or the framework error.

    systemframework gives an admin every user's systems in ownSystemArr (a company admin those
    of the company). SCOPE_MINE keeps only the signed-in user's own plus those shared with them,
    once each, sorted by name; SCOPE_ALL_VISIBLE keeps everything the role sees, as returned."""
    framework_data = await api_client.get_system_framework()
    if framework_data.get("status") == "error":
        return framework_data
    arrays = (('ownSystemArr', False), ('shareSystemArr', True)) if include_shared \
        else (('ownSystemArr', False),)
    entries, seen, unparsed = [], set(), 0
    for arr_name, shared in arrays:
        for system_json in framework_data.get(arr_name) or []:
            system_data = parse_system_json(
                system_json if isinstance(system_json, str) else json.dumps(system_json))
            key = _system_key(system_data) if system_data else {}
            if not key.get('systemName'):
                unparsed += 1
                continue
            if scope == SCOPE_MINE:
                ident = (key.get('userName'), key['systemName'])
                if (not shared and key.get('userName') != api_client.user_name) or ident in seen:
                    continue
                seen.add(ident)
            entries.append((system_data, key, shared))
    if unparsed:
        logger.warning(f"{unparsed} systemframework entries could not be parsed")
    if scope == SCOPE_MINE:
        entries.sort(key=lambda e: ((e[0].get('systemDisplayName') or e[1]['systemName']).lower(),
                                    e[1].get('userName') or ''))
    return {"status": "success", "entries": entries, "unparsedCount": unparsed}


def _system_entry(system_data: Dict[str, Any], key: Dict[str, Any], shared: bool,
                  user_name: str) -> Dict[str, Any]:
    """One system as list_all_systems returns it."""
    projects = system_data.get('projectDetailsList')
    owner = key.get('userName', 'Unknown')
    return {
        'systemName': key['systemName'],
        'systemDisplayName': system_data.get('systemDisplayName') or key['systemName'],
        'userName': owner,
        'environmentName': key.get('environmentName', 'All'),
        'type': AI_MODEL if system_data.get('isAW') else IT_SYSTEM,
        'timezone': system_data.get('timezone', 'US/Eastern'),
        'projectCount': len(projects) if isinstance(projects, list) else 0,
        'isShared': shared,
        'ownedByCurrentUser': owner == user_name,
    }


async def load_user_systems(api_client) -> Dict[str, Any]:
    """The signed-in user's systems (SCOPE_MINE), compact: {"status": "success", "systems"
    [{name, id, owner, type, timezone, projectCount, shared}], "unparsedCount"} or the error."""
    loaded = await _scoped_entries(api_client, SCOPE_MINE)
    if loaded.get("status") == "error":
        return loaded
    systems = []
    for system_data, key, shared in loaded["entries"]:
        e = _system_entry(system_data, key, shared, api_client.user_name)
        systems.append({'name': e['systemDisplayName'], 'id': e['systemName'], 'owner': e['userName'],
                        'type': e['type'], 'timezone': e['timezone'],
                        'projectCount': e['projectCount'], 'shared': shared})
    return {"status": "success", "systems": systems, "unparsedCount": loaded["unparsedCount"]}


def _scope_error(scope: str) -> Optional[Dict[str, Any]]:
    if scope in _SCOPES:
        return None
    return {"status": "error", "error_code": "invalid_argument",
            "message": f"scope must be one of {list(_SCOPES)}.",
            "next_step": f'Use scope="{SCOPE_MINE}" for the user\'s own systems or '
                         f'scope="{SCOPE_ALL_VISIBLE}" for every system their role can see.'}


_PREVIEW_PROJECT_NAMES = 10


def _render_system_list(systems: List[Dict[str, Any]], scope: str, user_name: str,
                        include_shared: bool = True) -> str:
    """The finished markdown answer for a complete system list: grouped by type, every line
    with its owner, so neither the owner nor a same-name IT System / AI Model pair gets lost
    when the list is shown. Systems carrying `projects` also list (up to 10) project names."""
    owned = sum(1 for s in systems if s['ownedByCurrentUser'])
    shared = sum(1 for s in systems if s['isShared'])
    by_type = {t: [s for s in systems if s['type'] == t] for t in (IT_SYSTEM, AI_MODEL)}
    if scope == SCOPE_MINE:
        title = f"## Your systems ({len(systems)})"
        counts = [f"{owned} owned by you"] + ([f"{shared} shared with you"] if include_shared
                                             else ["shared systems not included"])
    else:
        title = f"## Systems visible to {user_name} ({len(systems)})"
        counts = [f"{owned} owned by you", f"{len(systems) - owned} owned by other users"]
    counts += [f"{len(by_type[IT_SYSTEM])} IT Systems", f"{len(by_type[AI_MODEL])} AI Models"]
    parts = [title, " · ".join(counts)]
    for type_name, group in by_type.items():
        if not group:
            continue
        lines = []
        for i, s in enumerate(group, 1):
            line = f"{i}. **{s['systemDisplayName']}** (owner: {s['userName']}"
            line += ", shared with you)" if s['isShared'] else ")"
            if 'projects' in s:
                names = [p['projectName'] for p in s['projects']]
                line += f" · {len(names)} project{'' if len(names) == 1 else 's'}"
                if names:
                    line += ": " + ", ".join(names[:_PREVIEW_PROJECT_NAMES])
                    if len(names) > _PREVIEW_PROJECT_NAMES:
                        line += f" (+{len(names) - _PREVIEW_PROJECT_NAMES} more)"
            lines.append(line)
        parts.append(f"### {type_name}s ({len(group)})\n" + "\n".join(lines))
    if not systems:
        parts.append("No systems found.")
    if by_type[AI_MODEL]:
        parts.append("AI Models are monitored in AI WatchTower; incident, anomaly, metric, log "
                     "and dependency questions apply to IT Systems.")
    return "\n\n".join(parts)




# ============================================================================
# SYSTEM INFORMATION TOOLS
# ============================================================================

@mcp_server.tool()
async def list_all_systems(
    scope: Annotated[Literal["mine", "all_visible"], Field(
        description='"mine" (default): the signed-in user\'s own systems plus those shared with '
                    'them. "all_visible": every system the user\'s role can see (an admin: '
                    'every user\'s systems).')] = SCOPE_MINE,
    include_shared: bool = True,
    page: int = 1,
    page_size: Annotated[Optional[int], Field(
        description='Systems per page, 1-100. Default: 100 for scope="mine" (normally the whole '
                    'list in one page), 20 for scope="all_visible".')] = None,
) -> Dict[str, Any]:
    """
    List InsightFinder systems with their owner and type: by default the signed-in user's own
    systems, normally complete in one page.

    **Scope:** by default (scope="mine") only the signed-in user's systems: the person you are
    chatting with, the systems they own plus those other users shared with them. Admin roles do
    not widen it. scope="all_visible" lists every system the user's role can see instead: for an
    admin every user's systems in the environment, for a company admin every system of the
    company. Use it only when the user asks about other users' systems or the whole environment.

    Each system has a type, as in the UI's System Type filter: "IT System" (infrastructure and
    applications) or "AI Model" (an AI/ML or LLM model monitored by AI WatchTower). IT tools
    (incidents, anomalies, metrics, logs, dependency graph) work on IT Systems only; questions
    about an AI Model belong to AI WatchTower. ownedByCurrentUser marks the systems the user
    owns (the others in scope="mine" are shared with them); currentUser is their name.
    **When to use this tool:**
    - "What systems do I have?", "list my systems", "which systems do I own": the default scope;
      answer "own" questions from ownedByCurrentUser
    - Other users' systems or every system in the environment/company: scope="all_visible"
    - To discover systems or find the correct system name for other tools; for one possibly
      misspelled name, find_system_by_name is cheaper

    If pagination.hasMore is true, the list continues on the next page: say so, or fetch it,
    before calling an answer complete.

    ⚠️ When the whole list fits in one page, the `formatted_preview` field is the finished answer
    (every system with its owner, grouped by type). Return it as-is; do not rewrite it.

    Args:
        scope: "mine" (default) or "all_visible", see above
        include_shared: Whether to include systems shared with the user (default: True)
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Systems per page, 1-100 (default 100 for "mine", 20 for "all_visible")

    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - scope, currentUser (the signed-in user's name)
        - systems: [{systemName (id), systemDisplayName, userName (owner), environmentName, type,
          timezone, projectCount, isShared, ownedByCurrentUser}] for this page
        - ownedSystems / sharedSystems: the same page split by isShared
        - pagination: currentPage, pageSize, totalPages, totalCount, hasMore, ...
        - summary: totals of owned, shared, IT Systems and AI Models over all pages
        - formatted_preview: the finished markdown answer; only when the list is complete on
          page 1
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        bad_scope = _scope_error(scope)
        if bad_scope:
            return bad_scope
        if page_size is None:
            page_size = 100 if scope == SCOPE_MINE else 20

        # Validate pagination parameters
        if page < 1:
            return {
                "status": "error",
                "message": "Page number must be >= 1"
            }

        if page_size < 1 or page_size > 100:
            return {
                "status": "error",
                "message": "Page size must be between 1 and 100"
            }

        logger.info(f"Fetching system framework data (scope={scope}, page={page}, page_size={page_size})")

        loaded = await _scoped_entries(api_client, scope, include_shared)
        if loaded.get("status") == "error":
            return loaded
        all_systems = [_system_entry(d, k, shared, api_client.user_name)
                       for d, k, shared in loaded["entries"]]
        owned_systems = [x for x in all_systems if not x['isShared']]
        shared_systems = [x for x in all_systems if x['isShared']]

        total_count = len(all_systems)
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

        # Calculate pagination boundaries
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        # Check if page is out of range
        if start_idx >= total_count and total_count > 0:
            return {
                "status": "error",
                "message": f"Page {page} is out of range. Total pages: {total_pages}",
                "pagination": {
                    "totalPages": total_pages,
                    "totalCount": total_count
                }
            }

        # Get paginated slice
        paginated_systems = all_systems[start_idx:end_idx]

        # Separate back into owned and shared for response
        paginated_owned = [s for s in paginated_systems if not s['isShared']]
        paginated_shared = [s for s in paginated_systems if s['isShared']]

        # Build pagination message
        whose = "the user's own and shared" if scope == SCOPE_MINE else "all visible"
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_systems)} systems on this page, {total_count} {whose} systems in total)"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."

        result = {
            "status": "success",
            "message": pagination_msg,
            "scope": scope,
            "currentUser": api_client.user_name,
            "systems": paginated_systems,
            "ownedSystems": paginated_owned,
            "sharedSystems": paginated_shared if include_shared else [],
            "pagination": {
                "currentPage": page,
                "pageSize": page_size,
                "totalPages": total_pages,
                "totalCount": total_count,
                "itemsOnPage": len(paginated_systems),
                "hasMore": page < total_pages,
                "hasPrevious": page > 1,
                "nextPage": page + 1 if page < total_pages else None,
                "previousPage": page - 1 if page > 1 else None
            },
            "summary": {
                "totalOwnedSystems": len(owned_systems),
                "totalSharedSystems": len(shared_systems) if include_shared else 0,
                "totalItSystems": sum(1 for x in all_systems if x['type'] == IT_SYSTEM),
                "totalAiModels": sum(1 for x in all_systems if x['type'] == AI_MODEL),
                "unparsedEntries": loaded["unparsedCount"],
                "displayMessage": f"Total: {len(owned_systems)} owned systems, {len(shared_systems) if include_shared else 0} shared systems"
            }
        }
        # Only a complete list is a finished answer: mcp-service shows the last tool's preview
        # verbatim, so a page of a longer list would read as the whole list.
        if total_pages == 1:
            result["formatted_preview"] = _render_system_list(
                all_systems, scope, api_client.user_name, include_shared)
        return result

    except Exception as e:
        logger.error(f"Error listing systems: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to list systems: {str(e)}"
        }


@mcp_server.tool()
async def list_all_systems_and_projects(
    scope: Annotated[Literal["mine", "all_visible"], Field(
        description='"mine" (default): the signed-in user\'s own systems plus those shared with '
                    'them. "all_visible": every system the user\'s role can see (an admin: '
                    'every user\'s systems).')] = SCOPE_MINE,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    Systems with all their projects (paginated): by default the signed-in user's own systems.
    Use it when project details are needed; to list or count systems, list_all_systems is
    smaller and normally complete in one page.

    **Scope:** by default (scope="mine") only the signed-in user's systems: the person you are
    chatting with, the systems they own plus those other users shared with them. Admin roles do
    not widen it. scope="all_visible" lists every system the user's role can see instead: for an
    admin every user's systems in the environment, for a company admin every system of the
    company. Use it only when the user asks about other users' systems or the whole environment.

    Each system has a type, as in the UI's System Type filter: "IT System" (infrastructure and
    applications) or "AI Model" (an AI/ML or LLM model monitored by AI WatchTower). IT tools
    (incidents, anomalies, metrics, logs, dependency graph) work on IT Systems only; questions
    about an AI Model belong to AI WatchTower. ownedByCurrentUser marks the systems the user
    owns (the others in scope="mine" are shared with them); currentUser is their name.
    **Pagination**: Default page size is 20 systems per page. Each system includes ALL its
    projects. If pagination.hasMore is true, the list continues on the next page.
    ⚠️ When the whole list fits in one page, the `formatted_preview` field is the finished
    answer (every system with its owner and projects, grouped by type). Return it as-is; do not
    rewrite it.

    **When to use this tool:**
    - When user asks for a complete overview of their InsightFinder setup
    - To understand the relationship between systems and projects
    - To see which projects belong to which systems

    Args:
        scope: "mine" (default) or "all_visible", see above
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Number of systems per page (default: 20, max: 100)

    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - scope, currentUser (the signed-in user's name)
        - systems: systems as in list_all_systems, each with projects
          [{projectName, projectKey, userName, dataType, projectClassType}] (paginated)
        - pagination: Pagination information
        - summary: Overall statistics (total systems, projects, owners)
        - formatted_preview: the finished markdown answer; only when the list is complete on
          page 1
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        bad_scope = _scope_error(scope)
        if bad_scope:
            return bad_scope

        # Validate pagination parameters
        if page < 1:
            return {
                "status": "error",
                "message": "Page number must be >= 1"
            }

        if page_size < 1 or page_size > 100:
            return {
                "status": "error",
                "message": "Page size must be between 1 and 100"
            }

        logger.info(f"Fetching complete system and project hierarchy (scope={scope}, page={page}, page_size={page_size})")

        loaded = await _scoped_entries(api_client, scope)
        if loaded.get("status") == "error":
            return loaded

        all_systems = []
        total_projects = 0
        unique_owners = set()
        for system_data, key, shared in loaded["entries"]:
            system_info = _system_entry(system_data, key, shared, api_client.user_name)
            projects = []
            for project_data in system_data.get('projectDetailsList') or []:
                if isinstance(project_data, dict):
                    project_info = extract_project_info(project_data)
                    projects.append(project_info)
                    unique_owners.add(project_info['userName'])
            system_info['projects'] = projects
            all_systems.append(system_info)
            total_projects += len(projects)
            unique_owners.add(system_info['userName'])

        # Apply pagination
        total_count = len(all_systems)
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

        # Calculate pagination boundaries
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        # Check if page is out of range
        if start_idx >= total_count and total_count > 0:
            return {
                "status": "error",
                "message": f"Page {page} is out of range. Total pages: {total_pages}",
                "pagination": {
                    "totalPages": total_pages,
                    "totalCount": total_count
                }
            }

        # Get paginated slice
        paginated_systems = all_systems[start_idx:end_idx]

        # Calculate projects on current page
        projects_on_page = sum(len(s['projects']) for s in paginated_systems)

        # Build pagination message
        whose = "the user's own and shared" if scope == SCOPE_MINE else "all visible"
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_systems)} systems with {projects_on_page} projects on this page, {total_count} {whose} systems with {total_projects} projects in total)"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."

        result = {
            "status": "success",
            "message": pagination_msg,
            "scope": scope,
            "currentUser": api_client.user_name,
            "systems": paginated_systems,
            "pagination": {
                "currentPage": page,
                "pageSize": page_size,
                "totalPages": total_pages,
                "totalSystems": total_count,
                "systemsOnPage": len(paginated_systems),
                "projectsOnPage": projects_on_page,
                "hasMore": page < total_pages,
                "hasPrevious": page > 1,
                "nextPage": page + 1 if page < total_pages else None,
                "previousPage": page - 1 if page > 1 else None
            },
            "summary": {
                "totalSystems": total_count,
                "totalProjects": total_projects,
                "uniqueOwners": len(unique_owners),
                "ownersList": sorted(list(unique_owners))
            }
        }
        if total_pages == 1:
            result["formatted_preview"] = _render_system_list(all_systems, scope, api_client.user_name)
        return result

    except Exception as e:
        logger.error(f"Error listing systems and projects: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to list systems and projects: {str(e)}"
        }


@mcp_server.tool()
async def get_projects_for_system(
    system_name: str,
    use_fuzzy_match: bool = True,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    Get all projects within a specific system (paginated).
    
    This tool retrieves detailed information about all projects belonging to
    a specific system. It supports fuzzy matching to handle typos or case
    differences in the system name.
    
    **Pagination**: Projects are returned in pages to avoid overwhelming responses.
    Default page size is 20 projects.
    
    **When to use this tool:**
    - When user asks "what projects are in system X?"
    - To explore the contents of a specific system
    - Before querying project-specific data
    - When user knows the system but not the project names
    
    Args:
        system_name: The system display name or system ID to query
        use_fuzzy_match: Enable fuzzy matching for system name (default: True)
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Number of projects per page (default: 20, max: 100)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - systemInfo: Information about the matched system
        - projects: List of projects in the system (paginated)
        - pagination: Pagination information
        - matchInfo: Fuzzy match details (if fuzzy matching was used)
        
    Example:
        # Get projects for a system (with fuzzy matching)
        result = await get_projects_for_system(system_name="production system")
        
        # Get second page of projects
        result = await get_projects_for_system(system_name="production system", page=2)
        
        # Exact match only
        result = await get_projects_for_system(
            system_name="Production System",
            use_fuzzy_match=False
        )
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        # Validate pagination parameters
        if page < 1:
            return {
                "status": "error",
                "message": "Page number must be >= 1"
            }
        
        if page_size < 1 or page_size > 100:
            return {
                "status": "error",
                "message": "Page size must be between 1 and 100"
            }
        
        logger.info(f"Fetching projects for system: {system_name}")
        
        # Fetch system framework data
        framework_data = await api_client.get_system_framework()
        
        if framework_data.get("status") == "error":
            return framework_data
        
        # Collect all systems for searching
        all_systems = []
        
        for system_json in framework_data.get('ownSystemArr', []) + framework_data.get('shareSystemArr', []):
            system_data = parse_system_json(system_json)
            if system_data:
                system_info = extract_system_info(system_data)
                
                # Extract projects
                projects = []
                for project_data in system_data.get('projectDetailsList', []):
                    project_info = extract_project_info(project_data)
                    projects.append(project_info)
                
                system_info['projects'] = projects
                all_systems.append(system_info)
        
        # Try exact match first
        # Exact (case-sensitive) before case-insensitive: names may differ only by case.
        matched_system = next(
            (s for s in all_systems
             if s['systemDisplayName'] == system_name or s['systemName'] == system_name),
            None) or next(
            (s for s in all_systems if s['systemDisplayName'].lower() == system_name.lower()),
            None)
        
        # Try fuzzy match if exact match failed and fuzzy matching is enabled
        match_info = {}
        if not matched_system and use_fuzzy_match:
            matched_system = find_best_match(system_name, all_systems)
            if matched_system:
                match_info = {
                    "searchedFor": system_name,
                    "matchedTo": matched_system['systemDisplayName'],
                    "matchScore": matched_system['matchScore'],
                    "matchedField": matched_system['matchedField'],
                    "fuzzyMatchUsed": True
                }
        else:
            match_info = {
                "exactMatch": True,
                "fuzzyMatchUsed": False
            }
        
        if not matched_system:
            # System not found, provide suggestions
            available_names = [s['systemDisplayName'] for s in all_systems[:10]]
            return {
                "status": "error",
                "message": f"System '{system_name}' not found",
                "suggestions": available_names,
                "hint": "Try using list_all_systems to see all available systems"
            }
        
        # Apply pagination to projects
        all_projects = matched_system['projects']
        total_projects = len(all_projects)
        total_pages = (total_projects + page_size - 1) // page_size if total_projects > 0 else 1
        
        # Calculate pagination boundaries
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        
        # Check if page is out of range
        if start_idx >= total_projects and total_projects > 0:
            return {
                "status": "error",
                "message": f"Page {page} is out of range. Total pages: {total_pages}",
                "pagination": {
                    "totalPages": total_pages,
                    "totalProjects": total_projects
                }
            }
        
        # Get paginated slice
        paginated_projects = all_projects[start_idx:end_idx]
        
        # Build pagination message
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_projects)} projects on this page, {total_projects} total projects in system '{matched_system['systemDisplayName']}')"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."
        
        return {
            "status": "success",
            "message": pagination_msg,
            "systemInfo": {
                "systemDisplayName": matched_system['systemDisplayName'],
                "systemName": matched_system['systemName'],
                "userName": matched_system['userName'],
                "projectCount": total_projects
            },
            "projects": paginated_projects,
            "pagination": {
                "currentPage": page,
                "pageSize": page_size,
                "totalPages": total_pages,
                "totalProjects": total_projects,
                "projectsOnPage": len(paginated_projects),
                "hasMore": page < total_pages,
                "hasPrevious": page > 1,
                "nextPage": page + 1 if page < total_pages else None,
                "previousPage": page - 1 if page > 1 else None
            },
            "matchInfo": match_info
        }
        
    except Exception as e:
        logger.error(f"Error getting projects for system: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to get projects for system: {str(e)}"
        }


@mcp_server.tool()
async def find_system_by_name(
    search_term: str,
    max_results: int = 5
) -> Dict[str, Any]:
    """
    Find systems by name using fuzzy matching.
    
    This tool helps users find the correct system name when they don't know
    the exact name or have made typos. It returns the best matches ranked
    by similarity score.
    
    **When to use this tool:**
    - When user mentions a system name that might be incorrect
    - Before calling other tools that require exact system names
    - To help users discover the correct system name
    
    Args:
        search_term: The system name to search for (handles typos, case differences)
        max_results: Maximum number of matches to return (default: 5)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - matches: List of matching systems ranked by similarity
        - searchTerm: The original search term
        
    Example:
        # Find system with fuzzy name
        result = await find_system_by_name(search_term="insightfinder demo")
        
        # Result includes best matches:
        {
            "status": "success",
            "matches": [
                {
                    "systemDisplayName": "InsightFinder Demo System (APP)",
                    "matchScore": 0.85,
                    "userName": "admin",
                    ...
                }
            ]
        }
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        logger.info(f"Searching for system: {search_term}")
        
        # Fetch system framework data
        framework_data = await api_client.get_system_framework()
        
        if framework_data.get("status") == "error":
            return framework_data
        
        # Collect all systems
        all_systems = []
        
        for system_json in framework_data.get('ownSystemArr', []) + framework_data.get('shareSystemArr', []):
            system_data = parse_system_json(system_json)
            if system_data:
                system_info = extract_system_info(system_data)
                all_systems.append(system_info)
        
        # Calculate similarity scores for all systems
        scored_systems = []
        for system in all_systems:
            display_name = system.get('systemDisplayName', '')
            
            # Only compare with systemDisplayName (not systemName as it's a hash)
            match_score = calculate_similarity(search_term, display_name)
            
            # Create result without systemName (hash value)
            result_system = {
                'systemDisplayName': system.get('systemDisplayName'),
                'userName': system.get('userName'),
                'environmentName': system.get('environmentName'),
                'timezone': system.get('timezone'),
                'projectCount': system.get('projectCount'),
                'isShared': system.get('isShared'),
                'matchScore': match_score,
                'matchedField': 'systemDisplayName'
            }
            
            scored_systems.append(result_system)
        
        # Sort by score (descending) and limit results
        scored_systems.sort(key=lambda x: x['matchScore'], reverse=True)
        top_matches = scored_systems[:max_results]
        
        # Filter out very low scores (below 0.3)
        relevant_matches = [s for s in top_matches if s['matchScore'] >= 0.3]
        
        if not relevant_matches:
            return {
                "status": "error",
                "message": f"No systems found matching '{search_term}'",
                "searchTerm": search_term,
                "hint": "Try using list_all_systems to see all available systems"
            }
        
        return {
            "status": "success",
            "matches": relevant_matches,
            "searchTerm": search_term,
            "matchCount": len(relevant_matches)
        }
        
    except Exception as e:
        logger.error(f"Error finding system: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to find system: {str(e)}"
        }


@mcp_server.tool()
async def list_available_instances_for_project(
    project_name: str,
    page: int = 1,
    page_size: int = 50
) -> Dict[str, Any]:
    """
    List all available instances for a specific project (paginated).
    
    This tool retrieves the complete list of instance names that are available
    within a specific project. Use this to discover what instances exist in a project
    before querying metric data for specific instances.
    
    **When to use this tool:**
    - Before querying metric data, to see what instances are available in a project
    - When user asks "what instances are in this project?"
    - To help users understand the infrastructure covered by their project
    - When users need to know exact instance names for querying
    - When an invalid instance error occurs, to see valid options
    
    **Workflow:**
    1. Use list_all_systems_and_projects to find projects
    2. Use this tool to get available instances for a project
    3. Use get_metric_data with selected instance and metrics
    
    **Pagination**: Results are returned in pages to avoid overwhelming responses.
    Default page size is 50 instances.
    
    Args:
        project_name: Name or display name of the project to query (required)
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Number of instances per page (default: 50, max: 5000)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - projectName: Name of the queried project (actual projectName from API)
        - availableInstances: List of instance names available for this project (paginated)
        - pagination: Pagination information (currentPage, pageSize, totalPages, totalCount, hasMore)
        
    Example:
        # List first page of instances
        result = await list_available_instances_for_project(project_name="my-project")
        
        # List second page
        result = await list_available_instances_for_project(
            project_name="my-project", 
            page=2
        )
        
        # Custom page size
        result = await list_available_instances_for_project(
            project_name="my-project",
            page_size=100
        )
        
        # Response format:
        {
            "status": "success",
            "projectName": "my-project",
            "availableInstances": ["server-01", "server-02", ...],
            "pagination": {
                "currentPage": 1,
                "pageSize": 50,
                "totalPages": 3,
                "totalCount": 142,
                "hasMore": true
            }
        }
    """
    try:
        # Get current API client
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        # Validate pagination parameters
        if page < 1:
            return {
                "status": "error",
                "message": "Page number must be >= 1"
            }
        
        if page_size < 1 or page_size > 5000:
            return {
                "status": "error",
                "message": "Page size must be between 1 and 5000"
            }
        
        # Validate input
        if not project_name:
            return {
                "status": "error",
                "message": "project_name is a required parameter"
            }
        
        logger.info(f"Fetching available instances for project={project_name} (page={page}, page_size={page_size})")
        
        # Get project info including instance list
        project_info = await api_client.get_customer_name_for_project(project_name)
        
        if not project_info:
            return {
                "status": "error",
                "message": f"Project '{project_name}' not found. Please verify the project name or use list_all_systems_and_projects to see available projects."
            }
        
        customer_name, actual_project_name, display_project_name, instance_list, system_id, _ = project_info
        
        if not instance_list:
            return {
                "status": "success",
                "message": f"No instances found for project '{actual_project_name}'",
                "projectName": actual_project_name,
                "availableInstances": [],
                "instanceCount": 0,
                "pagination": {
                    "currentPage": 1,
                    "pageSize": page_size,
                    "totalPages": 0,
                    "totalCount": 0,
                    "hasMore": False
                }
            }
        
        # Calculate pagination
        total_count = len(instance_list)
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        # Check if page is out of range
        start_idx = (page - 1) * page_size
        if start_idx >= total_count and total_count > 0:
            return {
                "status": "error",
                "message": f"Page {page} is out of range. Total pages: {total_pages}",
                "pagination": {
                    "totalPages": total_pages,
                    "totalCount": total_count
                }
            }
        
        # Get paginated slice
        end_idx = start_idx + page_size
        paginated_instances = instance_list[start_idx:end_idx]
        
        # Build pagination message
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_instances)} instances on this page, {total_count} total instances)"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."
        
        return {
            "status": "success",
            "message": pagination_msg,
            "projectName": actual_project_name,
            "projectDisplayName": display_project_name,
            "customerName": customer_name,
            "availableInstances": paginated_instances,
            "instanceCount": len(paginated_instances),
            "pagination": {
                "currentPage": page,
                "pageSize": page_size,
                "totalPages": total_pages,
                "totalCount": total_count,
                "itemsOnPage": len(paginated_instances),
                "hasMore": page < total_pages,
                "hasPrevious": page > 1,
                "nextPage": page + 1 if page < total_pages else None,
                "previousPage": page - 1 if page > 1 else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching instances for project: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to fetch instances for project: {str(e)}"
        }


def _summary_section(title: str, res: Any, render) -> str:
    """One markdown bullet for a sub-result; a failed sub-call becomes a one-line note."""
    if isinstance(res, Exception):
        return f"**{title}:** unavailable ({type(res).__name__}: {res})"
    if not isinstance(res, dict) or res.get("status") != "success":
        return f"**{title}:** unavailable ({(res or {}).get('message', 'no data') if isinstance(res, dict) else res})"
    return f"**{title}:** {render(res)}"


def _top(d: Dict[str, int], n: int = 3) -> str:
    return ", ".join(f"{k} ({v:,})" for k, v in list(d.items())[:n]) if d else "none"


def _render_ari_investigations(overview: Any) -> str:
    """The on-call ARI section of a system summary, from get_incidents_overview's
    summary.ari_investigations: counts by status, then each Completed investigation with its
    report digest (overview, actions taken with links, next action). "" when ARI investigated
    nothing in the window."""
    if not isinstance(overview, dict) or overview.get("status") != "success":
        return ""
    ari = (overview.get("summary") or {}).get("ari_investigations")
    if not ari:
        return ""
    by_status = ", ".join(f"{n:,} {status}" for status, n in ari.get("by_status", {}).items())
    lines = [f"**ARI investigations:** {by_status}."]
    for inc in ari.get("completed", []):
        lines.append(f"- [{inc.get('timestamp_human')}]: {inc.get('component')} "
                     f"(Instance: {inc.get('instance')}) in {inc.get('projectDisplayName')} "
                     f"project. Detected issue: {inc.get('issue') or inc.get('pattern')}")
        lines.extend(render_ari_digest(inc.get("ari_digest") or {}))
    more = ari.get("completed_total", 0) - len(ari.get("completed", []))
    if more > 0:
        lines.append(f"- …and {more} more completed investigation(s); ask for an incident's "
                     f"ARI report to see it.")
    return "\n".join(lines)


@mcp_server.tool()
async def get_system_summary(
    system_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ONE-CALL summary of everything that happened in ONE system over a time range: incidents
    (totals, consolidated count, dominant patterns, affected projects), metric anomalies (totals,
    top patterns), log anomalies (every record retrieved via the paged API and reduced to a
    pattern digest), deployments / change events, and on-call ARI investigations (counts by
    status; each completed one with its overview, actions taken such as pull requests, and next
    action). All are fetched concurrently server-side and composed into ready-to-display markdown.

    **Use this tool FIRST, and usually ONLY, for a BROAD question about a named system** — one
    that names no specific incident, metric, project or action:
    - "What happened on 2026-06-09 in <system>?"  - "Summarize <system> for last week"
    - "How is <system> doing today?"               - "Any issues in <system> yesterday?"
    Do NOT also call get_incidents_overview / get_incidents_list / fetch_metric_anomalies /
    fetch_log_anomalies / get_deployments_* for the same question — this tool already includes
    them. For a question about ALL systems use showallsystemssummary.

    **Do NOT use this tool when the request is specific** — go straight to the specific tools:
    - a particular incident or a root cause analysis ("the incident at 02:15", "RCA") →
      get_incidents_list / get_incident_details / get_incident_raw_data
    - a metric line chart or a named metric → get_metric_data / get_project_metric_anomalies
    - one project's log anomalies → get_project_log_anomalies
    - creating or previewing a Jira ticket → preview_jira_ticket / create_jira_ticket
    A system name plus a time range alone does NOT make a question broad.

    ⚠️ The `formatted_preview` field is the finished answer. Return it as-is; do not rewrite it.
    A complete log-anomaly pattern table is appended to the final answer automatically.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 16") without a year,
    always default to year 2026.

    Args:
        system_name: The system to summarize.
        start_time: Start of the window. Accepts "2026-02-12T11:05:00", "2026-02-12",
                    "02/12/2026" or milliseconds. Default: 24 hours ago.
        end_time: End of the window, same formats. Default: now.
    """
    import asyncio
    from ..progress import report_progress
    from .incident_tools import get_incidents_overview, get_incidents_statistics, fetch_log_anomalies
    from .metric_anomaly_tools import get_metric_anomalies_overview
    from .deployment_tools import get_deployments_overview

    try:
        tz_name, system_name = await resolve_system_timezone(system_name)
        report_progress(f"Summarizing {system_name}: incidents, metrics, logs, deployments",
                        stage="fetch")
        inc, stats, met, logs, dep = await asyncio.gather(
            get_incidents_overview(system_name, start_time, end_time),
            get_incidents_statistics(system_name, start_time, end_time),
            get_metric_anomalies_overview(system_name, start_time, end_time),
            fetch_log_anomalies(system_name, start_time, end_time),
            get_deployments_overview(system_name, start_time, end_time),
            return_exceptions=True,
        )

        def render_incidents(r):
            s = r.get("summary", {})
            st = stats.get("statistics", {}) if isinstance(stats, dict) and stats.get("status") == "success" else {}
            out = (f"{s.get('total_incidents', 0):,} raw incidents, consolidated into "
                   f"{s.get('consolidated_incidents', 0):,}")
            if st.get("top_patterns"):
                out += f". Dominant patterns: {_top(st['top_patterns'])}"
            if st.get("top_affected_projects"):
                out += f". Most affected projects: {_top(st['top_affected_projects'])}"
            if s.get("first_event") and s.get("last_event"):
                out += f". Window {s['first_event'][11:16]} to {s['last_event'][11:16]}"
            return out + "."

        def render_metrics(r):
            s = r.get("summary", {})
            tops = ", ".join(f"{p['pattern'].removeprefix('metric_value_')} ({p['count']:,})"
                             for p in (s.get("top_patterns") or [])[:3])
            return (f"{s.get('total_anomalies', 0):,} anomalies across "
                    f"{s.get('unique_components', 0):,} components" + (f". Top: {tops}." if tops else "."))

        def render_logs(r):
            ps = r.get("pattern_summary") or {}
            un = ps.get("unnamed_anomalies") or {}
            groups = ", ".join(f"{g['message_group_not_a_pattern_name']} ({g['anomalies']:,})"
                               for g in (un.get("largest_unnamed_groups_by_message") or [])[:3])
            def _short(name: str, limit: int = 60) -> str:
                if len(name) <= limit:
                    return name
                cut = name[:limit].rsplit(" ", 1)[0].rstrip(" -,")
                return f"{cut}…"
            named = ", ".join(f"{_short(p['pattern_name'])} ({p['anomalies']:,})"
                              for p in (ps.get("top_named_patterns") or [])[:2])
            out = (f"{r.get('total_anomalies', 0):,} anomalies across "
                   f"{ps.get('total_instances', 0):,} instances")
            if r.get("data_source") == "paged" and r.get("total_anomalies"):
                out += " (every record retrieved)"
            if r.get("anomalies_by_project"):
                out += f". By project: {_top(r['anomalies_by_project'])}"
            if named:
                out += f". Top named patterns: {named}"
            if groups:
                out += f". Largest unnamed groups: {groups}"
            return out + (". Full pattern table below." if r.get("total_anomalies") else ".")

        def render_deployments(r):
            n = (r.get("summary") or {}).get("total_deployments", 0)
            return f"{n:,} in this window." if n else "none in this window."

        tr = (inc.get("time_range") if isinstance(inc, dict) else None) or \
             (logs.get("time_range") if isinstance(logs, dict) else None) or {}
        window = f"{tr.get('start_human', start_time or 'last 24h')} to {tr.get('end_human', end_time or 'now')}"
        preview = "\n\n".join([
            f"## What happened in {system_name} — {window}",
            _summary_section("Incidents", inc, render_incidents),
            _summary_section("Metric anomalies", met, render_metrics),
            _summary_section("Log anomalies", logs, render_logs),
            _summary_section("Deployments / change events", dep, render_deployments),
        ] + ([ari_section] if (ari_section := _render_ari_investigations(inc)) else []))

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": tr,
            "formatted_preview": preview,
            "verbatim_markdown": logs.get("verbatim_markdown", "") if isinstance(logs, dict) else "",
            "sections_failed": [name for name, r in
                                (("incidents", inc), ("incident_statistics", stats), ("metrics", met),
                                 ("logs", logs), ("deployments", dep))
                                if isinstance(r, Exception) or not (isinstance(r, dict) and r.get("status") == "success")],
        }
    except Exception as e:
        logger.error(f"Error in get_system_summary: {e}", exc_info=True)
        return {"status": "error", "message": f"Error in get_system_summary: {e}"}


@mcp_server.tool()
async def showallsystemssummary(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a pre-composed LLM markdown summary of all systems' incidents, anomalies, and
    change events for the specified time period.

    **Use this tool when the user asks for a summary such as:**
    - "Give me a summary for today / yesterday / this week / last month"
    - "What happened on 2026-04-08?"
    - "Show me a summary for April" / "Summarise last week"
    - "What issues were there between [date] and [date]?"
    - Any request for an overall summary over a time period across ALL systems.
    For a summary of ONE named system ("what happened on [date] in [system]"), use
    get_system_summary instead — it is system-scoped and includes the log-anomaly digest.

    ⚠️ IMPORTANT: The `formatted_preview` field is the finished answer (it includes each
    incident's ARI investigation, pull request and report links). Return it as-is; do not
    rewrite, reformat or summarise it.

    ⚠️ RELATIVE DATE KEYWORDS SUPPORTED:
    - "today": Today's date (full day)  [DEFAULT if omitted]
    - "yesterday": Yesterday's date (full day)
    - "thisweek" / "this_week": Monday to today
    - "lastweek" / "last_week": Last Monday to Last Sunday
    - "thismonth" / "this_month": 1st of current month to today
    - "lastmonth" / "last_month": 1st of last month to last day of last month
    - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds

    Args:
        start_time: Start of the time window (default: today). Accepts relative keywords or absolute dates.
        end_time: End of the time window (default: today). Accepts relative keywords or absolute dates.

    Returns:
        {"status": "success", "formatted_preview": ready-to-display markdown (a one-line issue
        summary, a per-system breakdown table and detailed incident/anomaly listings),
        "totals": {...}, "time_range": {...}, "citations": [one per listed system]}
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {"status": "error",
                    "message": "No API client configured. Please configure your InsightFinder credentials."}

        # Resolve owner timezone (uses first available system)
        try:
            tz_name, _ = await resolve_system_timezone("")
        except Exception:
            tz_name = "UTC"

        # Parse / default time range
        try:
            start_ms, end_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if start_ms is None or end_ms is None:
            start_ms, end_ms = get_time_range_ms(tz_name, 1)

        # Build a human-readable timeframe label
        try:
            start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if start_dt == end_dt:
                timeframe_label = f"on {start_dt}"
            else:
                timeframe_label = f"from {start_dt} to {end_dt}"
        except Exception:
            timeframe_label = "for the requested period"

        url = f"{api_client.base_url}/api/v1/mcp-summary"
        params = {
            "startTime": start_ms,
            "endTime": end_ms,
        }
        headers = {
            "X-User-Name": api_client.user_name,
            "X-License-Key": api_client.license_key,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        total_incidents = data.get("totalIncidents", 0)
        total_metric = data.get("totalMetricAnomalies", 0)
        total_log = data.get("totalLogAnomalies", 0)
        total_change = data.get("totalChangeEvents", 0)
        total_issues = total_incidents + total_metric + total_log + total_change
        summary_markdown = data.get("summaryMarkdown", "")

        # Newer backends send `ariSummary` (ARI's own one-paragraph account: predictions, lead
        # time, prevented and investigated incidents, alerts consolidated into tickets) and no
        # longer send the metric/log anomaly totals, so the old count line would read
        # "0 Metric Anomalies". Lead with ariSummary when present. The backend always words it
        # "so far today"; for any other window, say which one.
        ari_summary = (data.get("ariSummary") or "").strip()
        if ari_summary:
            try:
                today = datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
            except Exception:
                today = None
            if timeframe_label != f"on {today}":
                ari_summary = ari_summary.replace(" so far today", f" {timeframe_label}")
            header_line = ari_summary
        else:
            header_line = (
                f"I have identified **{total_issues}** issues {timeframe_label}. "
                f"{total_incidents} Incidents, {total_metric} Metric Anomalies, "
                f"{total_log} Log Anomalies, {total_change} Change Events"
            )

        return {
            "status": "success",
            "formatted_preview": f"{header_line}\n\n{summary_markdown}",
            "totals": {
                "issues": total_issues,
                "incidents": total_incidents,
                "metric_anomalies": total_metric,
                "log_anomalies": total_log,
                "change_events": total_change,
                "validated_predictions": data.get("totalValidatedPredictions"),
                "prevented_incidents": data.get("totalPreventedIncidents"),
                "investigated_incidents": data.get("totalInvestigatedIncidents"),
            },
            "time_range": {
                "start": start_ms,
                "end": end_ms,
                "start_human": format_timestamp_in_user_timezone(start_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_ms, tz_name),
            },
            # Structured, one per listed system (same schema as mcp-service citations).
            "citations": data.get("citations") or [],
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching all-systems summary: {e}", exc_info=True)
        return {"status": "error",
                "message": f"API request failed with status {e.response.status_code}: {e.response.text}"}
    except Exception as e:
        logger.error(f"Error fetching all-systems summary: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Failed to fetch all-systems summary: {str(e)}"}
