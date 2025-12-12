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
    include_shared: bool = True
) -> Dict[str, Any]:
    """
    List all available InsightFinder systems with their basic information.
    
    This tool retrieves a list of all systems accessible to the current user,
    including both owned systems and optionally shared systems. Each system
    entry includes the system name, display name, owner, and project count.
    
    **When to use this tool:**
    - When user asks "what systems do I have?"
    - To discover available systems before querying specific data
    - To find the correct system name for other tools
    - When user mentions a system name that might be incorrect
    
    Args:
        include_shared: Whether to include systems shared with the user (default: True)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - ownedSystems: List of systems owned by the user
        - sharedSystems: List of systems shared with the user (if include_shared=True)
        - totalCount: Total number of systems
        
    Example:
        # List all systems
        result = await list_all_systems()
        
        # List only owned systems
        result = await list_all_systems(include_shared=False)
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }
        
        logger.info("Fetching system framework data")
        
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
        
        result = {
            "status": "success",
            "ownedSystems": owned_systems,
            "ownedSystemsCount": len(owned_systems)
        }
        
        if include_shared:
            result["sharedSystems"] = shared_systems
            result["sharedSystemsCount"] = len(shared_systems)
        
        result["totalCount"] = len(owned_systems) + (len(shared_systems) if include_shared else 0)
        
        return result
        
    except Exception as e:
        logger.error(f"Error listing systems: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to list systems: {str(e)}"
        }


@mcp_server.tool()
async def list_all_systems_and_projects() -> Dict[str, Any]:
    """
    Get comprehensive information about all systems and their projects.
    
    This tool retrieves detailed information about all accessible systems,
    including all projects within each system. This provides a complete
    view of the InsightFinder hierarchy: systems -> projects.
    
    **When to use this tool:**
    - When user asks for a complete overview of their InsightFinder setup
    - To understand the relationship between systems and projects
    - To see which projects belong to which systems
    - When user needs detailed project information across all systems
    
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - systems: List of systems with their projects
        - summary: Overall statistics (total systems, projects, owners)
        
    Example:
        # Get all systems and projects
        result = await list_all_systems_and_projects()
        
        # Response includes full hierarchy:
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
        
        logger.info("Fetching complete system and project hierarchy")
        
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
        
        return {
            "status": "success",
            "systems": all_systems,
            "summary": {
                "totalSystems": len(all_systems),
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
    use_fuzzy_match: bool = True
) -> Dict[str, Any]:
    """
    Get all projects within a specific system.
    
    This tool retrieves detailed information about all projects belonging to
    a specific system. It supports fuzzy matching to handle typos or case
    differences in the system name.
    
    **When to use this tool:**
    - When user asks "what projects are in system X?"
    - To explore the contents of a specific system
    - Before querying project-specific data
    - When user knows the system but not the project names
    
    Args:
        system_name: The system display name or system ID to query
        use_fuzzy_match: Enable fuzzy matching for system name (default: True)
        
    Returns:
        A dictionary containing:
        - status: "success" or "error"
        - systemInfo: Information about the matched system
        - projects: List of projects in the system
        - matchInfo: Fuzzy match details (if fuzzy matching was used)
        
    Example:
        # Get projects for a system (with fuzzy matching)
        result = await get_projects_for_system(system_name="production system")
        
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
        if not matched_system and use_fuzzy_match:
            matched_system = find_best_match(system_name, all_systems)
            
            if matched_system:
                return {
                    "status": "success",
                    "systemInfo": {
                        "systemDisplayName": matched_system['systemDisplayName'],
                        "systemName": matched_system['systemName'],
                        "userName": matched_system['userName'],
                        "projectCount": len(matched_system['projects'])
                    },
                    "projects": matched_system['projects'],
                    "matchInfo": {
                        "searchedFor": system_name,
                        "matchedTo": matched_system['systemDisplayName'],
                        "matchScore": matched_system['matchScore'],
                        "matchedField": matched_system['matchedField'],
                        "fuzzyMatchUsed": True
                    }
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
        
        return {
            "status": "success",
            "systemInfo": {
                "systemDisplayName": matched_system['systemDisplayName'],
                "systemName": matched_system['systemName'],
                "userName": matched_system['userName'],
                "projectCount": len(matched_system['projects'])
            },
            "projects": matched_system['projects'],
            "matchInfo": {
                "exactMatch": True,
                "fuzzyMatchUsed": False
            }
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
