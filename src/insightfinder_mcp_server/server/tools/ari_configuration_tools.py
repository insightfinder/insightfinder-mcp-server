"""
ARI configuration tools for the InsightFinder MCP server.

Tools:
- getARIModelInfo: Fetch supported model types and/or versions
- setupARIConfiguration: Create or update ARI (LLM) model settings
- setDefaultARIModel: Set an already-configured model as the active default
- deleteARIConfiguration: Delete an ARI model configuration
"""

import logging
from typing import Dict, Any, Optional

import httpx

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

logger = logging.getLogger(__name__)

LLM_SERVER_URLS = {
    "gemini": "https://gemini.google.com",
    "openai": "https://openai.com",
    "anthropic": "https://api.anthropic.com",
    "aws bedrock": "https://bedrock.amazonaws.com",
}

_ARI_BASE = "/api/external/v1"


def _get_api_client():
    api_client = get_current_api_client()
    if not api_client:
        return None, {
            "status": "error",
            "message": "No API client configured. Please configure your InsightFinder credentials.",
        }
    return api_client, None  # type: ignore[return-value]


async def _fetch_valid_types(base_url: str, headers: dict) -> tuple[list, Optional[dict]]:
    """Returns (valid_types_list, error_dict_or_None)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}{_ARI_BASE}/mcp-model-types", headers=headers, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return [], {"status": "error", "message": f"Failed to fetch model types: {e}"}

    if data.get("success") and isinstance(data.get("result"), list):
        return data["result"], None
    return [], {"status": "error", "message": "Unexpected response format from mcp-model-types."}


async def _fetch_valid_versions(base_url: str, headers: dict, model_type: str) -> tuple[list, list, Optional[dict]]:
    """Returns (versions_list, groups_list, error_dict_or_None).
    versions_list is a flat list of valid version IDs.
    groups_list is the raw grouped structure from the API.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{base_url}{_ARI_BASE}/mcp-model-versions",
                params={"modelType": model_type},
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return [], [], {"status": "error", "message": f"Failed to fetch model versions: {e}"}

    if data.get("success") and isinstance(data.get("result"), list):
        groups = data["result"]
        versions = [v for group in groups for v in group.get("versions", [])]
        return versions, groups, None
    return [], [], {"status": "error", "message": "Unexpected response format from mcp-model-versions."}


@mcp_server.tool()
async def getARIModelInfo(modelType: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve supported ARI model types and/or versions from InsightFinder.

    **When to use this tool:**
    - To discover which LLM providers (OpenAI, Anthropic, Gemini) are supported
    - To list available model versions for a specific provider before configuring ARI
    - To look up valid modelVersion values required by setupARIConfiguration

    Args:
        modelType: Optional. If omitted, returns all supported provider types.
                   If provided (e.g. "OpenAI", "Anthropic", "Gemini"), returns the
                   grouped model versions (default + fine-tuned) for that provider.

    Returns:
        - Without modelType: {"status": "success", "modelTypes": ["OpenAI", "Anthropic", "Gemini"]}
        - With modelType:    {"status": "success", "modelType": "OpenAI", "modelGroups": [...]}
    """
    api_client, err = _get_api_client()
    if err:
        return err
    assert api_client is not None

    base_url = api_client.base_url
    headers = api_client.headers

    if not modelType:
        valid_types, err = await _fetch_valid_types(base_url, headers)
        if err:
            return err
        return {"status": "success", "modelTypes": valid_types}

    # Validate the modelType first
    valid_types, err = await _fetch_valid_types(base_url, headers)
    if err:
        return err

    matched_type = next((t for t in valid_types if t.lower() == modelType.lower()), None)
    if not matched_type:
        return {
            "status": "error",
            "message": f"Invalid modelType '{modelType}'. Valid types: {', '.join(valid_types)}",
            "validModelTypes": valid_types,
        }

    _, groups, err = await _fetch_valid_versions(base_url, headers, matched_type)
    if err:
        return err

    return {
        "status": "success",
        "modelType": matched_type,
        "modelGroups": groups,
    }


async def _verify_configuration_saved(base_url: str, headers: dict, model_type: str, model_version: str) -> tuple[bool, Optional[dict]]:
    """Returns (found, error_dict_or_None). Calls GET /mcp-model-setting to confirm the entry persisted."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{base_url}{_ARI_BASE}/mcp-model-setting",
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return False, {"status": "error", "message": f"Failed to verify configuration was saved: {e}"}

    if not data.get("success"):
        return False, {"status": "error", "message": "Verification call to mcp-model-setting did not return success."}

    # API returns the list under "settings" (not "result")
    result = data.get("settings") or data.get("result", [])
    if not isinstance(result, list):
        result = [result] if result else []

    found = any(
        str(entry.get("modelType", "")).lower() == model_type.lower()
        and str(entry.get("modelVersion", "")) == model_version
        for entry in result
    )
    return found, None


async def _set_default_model(base_url: str, headers: dict, model_type: str, model_version: str) -> Optional[dict]:
    """Calls POST /mcp-model-setting-used-model to mark model as default. Returns error dict or None."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}{_ARI_BASE}/mcp-model-setting-used-model",
                data={"modelType": model_type, "modelVersion": model_version, "isCurrentUserModel": "true"},
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return {"status": "error", "message": f"Failed to set default model — HTTP {e.response.status_code}: {e.response.text}"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to set default model: {e}"}

    if not data.get("success", False):
        return {"status": "error", "message": data.get("message", "Server rejected the set-default request."), "serverResponse": data}
    return None


@mcp_server.tool()
async def setupARIConfiguration(
    modelType: str,
    llmApiKey: str,
    modelVersion: str,
    mcpApiKey: str,
    mcpServerUrl: str = "https://mcp.insightfinder.com",
    llmServerUrl: Optional[str] = None,
    update: bool = False,
    isCurrentUserModel: bool = False,
) -> Dict[str, Any]:
    """
    Create or update the ARI (LLM) model configuration in InsightFinder.

    This tool validates the model type and version against InsightFinder's supported
    models, then submits the configuration via the mcp-model-setting API. After saving,
    it verifies the configuration was persisted before returning success.

    **When to use this tool:**
    - To configure which LLM model ARI should use (new setup)
    - To change the active model, API key, or server URL (update existing config)

    **Supported model types:** OpenAI, Anthropic, Gemini

    **Default LLM server URLs (auto-selected from modelType if not provided):**
    - OpenAI    → https://openai.com
    - Anthropic → https://api.anthropic.com
    - Gemini    → https://gemini.google.com

    Use getARIModelInfo to discover valid modelType and modelVersion values.

    Args:
        modelType: LLM provider. One of: OpenAI, Anthropic, Gemini (required)
        llmApiKey: API key for the LLM provider (required)
        modelVersion: Model version ID from the provider's versions list (required).
                      Must match a value in "versions" (not "modelVersionDisplayNames").
        mcpApiKey: API key for the MCP server (required)
        mcpServerUrl: MCP server URL (default: https://mcp.insightfinder.com)
        llmServerUrl: LLM provider base URL. Auto-detected from modelType if omitted.
        update: Set to True to update an existing configuration (default: False = create new).
        isCurrentUserModel: Set to True to mark this model as the active default after saving.

    Returns:
        - status: "success" or "error"
        - message: Human-readable result
        - configuration: The submitted config (on success)
        - serverResponse: Raw response from InsightFinder (on success)
    """
    api_client, err = _get_api_client()
    if err:
        return err
    assert api_client is not None

    base_url = api_client.base_url
    headers = api_client.headers

    # Validate modelType
    valid_types, err = await _fetch_valid_types(base_url, headers)
    if err:
        return err

    matched_type = next((t for t in valid_types if t.lower() == modelType.lower()), None)
    if not matched_type:
        return {
            "status": "error",
            "message": f"Invalid modelType '{modelType}'. Valid types: {', '.join(valid_types)}",
            "validModelTypes": valid_types,
        }
    modelType = matched_type

    # Resolve llmServerUrl
    if not llmServerUrl:
        llmServerUrl = LLM_SERVER_URLS.get(modelType.lower())
        if not llmServerUrl:
            return {
                "status": "error",
                "message": (
                    f"Could not determine llmServerUrl for modelType '{modelType}'. "
                    "Please provide it explicitly."
                ),
            }

    # Validate modelVersion against the versions list (not display names)
    valid_versions, _, err = await _fetch_valid_versions(base_url, headers, modelType)
    if err:
        return err

    if modelVersion not in valid_versions:
        return {
            "status": "error",
            "message": (
                f"Invalid modelVersion '{modelVersion}' for modelType '{modelType}'. "
                f"Use getARIModelInfo(modelType='{modelType}') to see valid versions."
            ),
            "validVersions": valid_versions,
        }

    # Submit configuration
    form_data = {
        "mcpServerUrl": mcpServerUrl,
        "mcpApiKey": mcpApiKey,
        "modelType": modelType,
        "llmServerUrl": llmServerUrl,
        "llmApiKey": llmApiKey,
        "isCreated": "false" if update else "true",
        "modelVersion": modelVersion,
        "modelVersionDisplayName": modelVersion,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}{_ARI_BASE}/mcp-model-setting",
                data=form_data,
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            result_data = resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "status": "error",
            "message": f"InsightFinder returned HTTP {e.response.status_code}: {e.response.text}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to submit configuration: {e}"}

    if not result_data.get("success", False):
        return {
            "status": "error",
            "message": result_data.get("message", "InsightFinder rejected the configuration."),
            "serverResponse": result_data,
        }

    # Verify the configuration was actually persisted in the database
    saved, err = await _verify_configuration_saved(base_url, headers, modelType, modelVersion)
    if err:
        return err
    if not saved:
        return {
            "status": "error",
            "message": (
                f"Configuration POST succeeded but the entry for {modelType}/{modelVersion} "
                "was not found when verifying. The setting may not have been saved."
            ),
            "serverResponse": result_data,
        }

    # Optionally mark this model as the default
    if isCurrentUserModel:
        err = await _set_default_model(base_url, headers, modelType, modelVersion)
        if err:
            return err

    action = "updated" if update else "created"
    default_note = " Set as default model." if isCurrentUserModel else ""
    return {
        "status": "success",
        "message": f"ARI configuration {action} successfully. Model: {modelType} / {modelVersion}.{default_note}",
        "configuration": {
            "mcpServerUrl": mcpServerUrl,
            "mcpApiKey": mcpApiKey,
            "modelType": modelType,
            "llmServerUrl": llmServerUrl,
            "modelVersion": modelVersion,
            "isCurrentUserModel": isCurrentUserModel,
        },
        "serverResponse": result_data,
    }


@mcp_server.tool()
async def setDefaultARIModel(
    modelType: str,
    modelVersion: str,
) -> Dict[str, Any]:
    """
    Set an already-configured ARI model as the active default.

    **When to use this tool:**
    - To switch which configured model ARI uses by default
    - When multiple models are configured and you want to change the active one

    Use getARIModelInfo to discover valid modelType and modelVersion values.
    The model must already be configured via setupARIConfiguration before calling this.

    Args:
        modelType: LLM provider of the target configuration (e.g. "OpenAI")
        modelVersion: Exact model version ID to set as default (e.g. "gpt-4.1")

    Returns:
        - status: "success" or "error"
        - message: Human-readable result
        - serverResponse: Raw response from InsightFinder (on success)
    """
    api_client, err = _get_api_client()
    if err:
        return err
    assert api_client is not None

    base_url = api_client.base_url
    headers = api_client.headers

    # Confirm the configuration actually exists before trying to set it as default
    saved, err = await _verify_configuration_saved(base_url, headers, modelType, modelVersion)
    if err:
        return err
    if not saved:
        return {
            "status": "error",
            "message": (
                f"No existing configuration found for {modelType}/{modelVersion}. "
                "Use setupARIConfiguration to add it first."
            ),
        }

    err = await _set_default_model(base_url, headers, modelType, modelVersion)
    if err:
        return err

    return {
        "status": "success",
        "message": f"{modelType}/{modelVersion} is now the active default ARI model.",
    }


@mcp_server.tool()
async def deleteARIConfiguration(
    modelType: str,
    modelVersion: str,
) -> Dict[str, Any]:
    """
    Delete an ARI model configuration from InsightFinder.

    **When to use this tool:**
    - To remove an existing LLM model configuration for ARI
    - When decommissioning or replacing a model setup

    Use getARIModelInfo to confirm valid modelType and modelVersion values before deleting.

    Args:
        modelType: LLM provider type of the configuration to delete (e.g. "OpenAI")
        modelVersion: Exact model version ID of the configuration to delete (e.g. "gpt-4o")

    Returns:
        - status: "success" or "error"
        - message: Human-readable result
        - serverResponse: Raw response from InsightFinder (on success)
    """
    api_client, err = _get_api_client()
    if err:
        return err
    assert api_client is not None

    base_url = api_client.base_url
    headers = api_client.headers

    # Validate modelType
    valid_types, err = await _fetch_valid_types(base_url, headers)
    if err:
        return err

    matched_type = next((t for t in valid_types if t.lower() == modelType.lower()), None)
    if not matched_type:
        return {
            "status": "error",
            "message": f"Invalid modelType '{modelType}'. Valid types: {', '.join(valid_types)}",
            "validModelTypes": valid_types,
        }
    modelType = matched_type

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{base_url}{_ARI_BASE}/mcp-model-setting",
                params={"modelType": modelType, "modelVersion": modelVersion},
                headers=headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            result_data = resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "status": "error",
            "message": f"InsightFinder returned HTTP {e.response.status_code}: {e.response.text}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete configuration: {e}"}

    if not result_data.get("success", False):
        return {
            "status": "error",
            "message": result_data.get("message", "InsightFinder rejected the delete request."),
            "serverResponse": result_data,
        }

    return {
        "status": "success",
        "message": f"ARI configuration deleted. Model: {modelType} / {modelVersion}",
        "serverResponse": result_data,
    }
