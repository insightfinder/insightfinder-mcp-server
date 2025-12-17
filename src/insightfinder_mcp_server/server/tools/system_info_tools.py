"""
System information tools for the InsightFinder MCP server.

This module provides tools for querying and exploring InsightFinder systems and projects:
- list_all_systems: List all available systems with their owners
- list_all_systems_and_projects: Get detailed information about all systems and their projects
- get_projects_for_system: Get all projects within a specific system
- find_system_by_name: Fuzzy search for system names (handles typos and case differences)

These tools help users discover and navigate the InsightFinder system hierarchy.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from difflib import SequenceMatcher

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

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
# SYSTEM INFORMATION TOOLS
# ============================================================================

@mcp_server.tool()
async def list_all_systems(
    include_shared: bool = True,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    List all available InsightFinder systems with their basic information (paginated).
    
    This tool retrieves a list of all systems accessible to the current user,
    including both owned systems and optionally shared systems. Each system
    entry includes the system name, display name, owner, and project count.
    
    **Pagination**: Results are returned in pages to avoid overwhelming responses.
    Default page size is 20 systems. Use the page parameter to navigate through results.
    
    **When to use this tool:**
    - When user asks "what systems do I have?"
    - To discover available systems before querying specific data
    - To find the correct system name for other tools
    - When user mentions a system name that might be incorrect
    
    Args:
        include_shared: Whether to include systems shared with the user (default: True)
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Number of systems per page (default: 20, max: 100)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - ownedSystems: List of systems owned by the user (paginated)
        - sharedSystems: List of systems shared with the user (paginated, if include_shared=True)
        - pagination: Pagination information (currentPage, pageSize, totalPages, totalCount, hasMore)
        
    Example:
        # List first page of systems
        result = await list_all_systems()
        
        # List second page
        result = await list_all_systems(page=2)
        
        # List only owned systems with custom page size
        result = await list_all_systems(include_shared=False, page_size=10)
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
        
        logger.info(f"Fetching system framework data (page={page}, page_size={page_size})")
        
        # Fetch system framework data
        framework_data = await api_client.get_system_framework()
        
        if framework_data.get("status") == "error":
            return framework_data
        
        owned_systems = []
        shared_systems = []
        
        # Process owned systems
        for system_json in framework_data.get('ownSystemArr', []):
            system_data = parse_system_json(system_json)
            if system_data:
                system_info = extract_system_info(system_data)
                system_info['isShared'] = False
                owned_systems.append(system_info)
        
        # Process shared systems if requested
        if include_shared:
            for system_json in framework_data.get('shareSystemArr', []):
                system_data = parse_system_json(system_json)
                if system_data:
                    system_info = extract_system_info(system_data)
                    system_info['isShared'] = True
                    shared_systems.append(system_info)
        
        # Combine and paginate
        all_systems = owned_systems + (shared_systems if include_shared else [])
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
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_systems)} systems on this page, {total_count} total systems)"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."
        
        result = {
            "status": "success",
            "message": pagination_msg,
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
                "displayMessage": f"Total: {len(owned_systems)} owned systems, {len(shared_systems) if include_shared else 0} shared systems"
            }
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing systems: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to list systems: {str(e)}"
        }


@mcp_server.tool()
async def list_all_systems_and_projects(
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    Get comprehensive information about all systems and their projects (paginated).
    
    This tool retrieves detailed information about all accessible systems,
    including all projects within each system. This provides a complete
    view of the InsightFinder hierarchy: systems -> projects.
    
    **Pagination**: Results are returned in pages to avoid overwhelming responses.
    Default page size is 20 systems per page. Each system includes ALL its projects.
    
    **When to use this tool:**
    - When user asks for a complete overview of their InsightFinder setup
    - To understand the relationship between systems and projects
    - To see which projects belong to which systems
    - When user needs detailed project information across all systems
    
    Args:
        page: Page number to retrieve (1-indexed, default: 1)
        page_size: Number of systems per page (default: 20, max: 100)
    
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - systems: List of systems with their projects (paginated)
        - pagination: Pagination information
        - summary: Overall statistics (total systems, projects, owners)
        
    Example:
        # Get first page of systems and projects
        result = await list_all_systems_and_projects()
        
        # Get second page
        result = await list_all_systems_and_projects(page=2)
        
        # Response includes full hierarchy (paginated):
        {
            "status": "success",
            "systems": [
                {
                    "systemDisplayName": "Production System",
                    "systemName": "abc123...",
                    "userName": "admin",
                    "projects": [
                        {
                            "projectName": "web-metrics",
                            "dataType": "Metric",
                            "userName": "admin"
                        },
                        ...
                    ]
                },
                ...
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
        
        logger.info(f"Fetching complete system and project hierarchy (page={page}, page_size={page_size})")
        
        # Fetch system framework data
        framework_data = await api_client.get_system_framework()
        
        if framework_data.get("status") == "error":
            return framework_data
        
        all_systems = []
        total_projects = 0
        unique_owners = set()
        
        # Process owned systems
        for system_json in framework_data.get('ownSystemArr', []):
            system_data = parse_system_json(system_json)
            if system_data:
                system_info = extract_system_info(system_data)
                system_info['isShared'] = False
                
                # Extract projects
                projects = []
                for project_data in system_data.get('projectDetailsList', []):
                    project_info = extract_project_info(project_data)
                    projects.append(project_info)
                    unique_owners.add(project_info['userName'])
                
                system_info['projects'] = projects
                all_systems.append(system_info)
                total_projects += len(projects)
                unique_owners.add(system_info['userName'])
        
        # Process shared systems
        for system_json in framework_data.get('shareSystemArr', []):
            system_data = parse_system_json(system_json)
            if system_data:
                system_info = extract_system_info(system_data)
                system_info['isShared'] = True
                
                # Extract projects
                projects = []
                for project_data in system_data.get('projectDetailsList', []):
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
        pagination_msg = f"Showing page {page} of {total_pages} ({len(paginated_systems)} systems with {projects_on_page} projects on this page, {total_count} total systems with {total_projects} total projects)"
        if page < total_pages:
            pagination_msg += f". There are {total_pages - page} more page(s) available. Use page={page + 1} to see the next page."
        
        return {
            "status": "success",
            "message": pagination_msg,
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
        matched_system = None
        for system in all_systems:
            if (system['systemDisplayName'] == system_name or 
                system['systemName'] == system_name or
                system['systemDisplayName'].lower() == system_name.lower()):
                matched_system = system
                break
        
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
    - When user says "show me incidents in citizen cane" but means "Citizen Cane Demo System"
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
        result = await find_system_by_name(search_term="citizen cane")
        
        # Result includes best matches:
        {
            "status": "success",
            "matches": [
                {
                    "systemDisplayName": "Citizen Cane Demo System (STG)",
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
            system_name = system.get('systemName', '')
            
            score_display = calculate_similarity(search_term, display_name)
            score_system = calculate_similarity(search_term, system_name)
            
            best_score = max(score_display, score_system)
            
            scored_systems.append({
                **system,
                'matchScore': best_score,
                'matchedField': 'systemDisplayName' if score_display > score_system else 'systemName'
            })
        
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
        page_size: Number of instances per page (default: 50, max: 500)
        
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
        
        if page_size < 1 or page_size > 500:
            return {
                "status": "error",
                "message": "Page size must be between 1 and 500"
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
        
        customer_name, actual_project_name, instance_list, system_id = project_info
        
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
