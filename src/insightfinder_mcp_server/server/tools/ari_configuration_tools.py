"""
ARI configuration tools for the InsightFinder MCP server.

This module provides tools for setting up ARI (AI/LLM) configurations:
- setupARIConfiguration: Configure MCP model settings for ARI
"""

import logging
from typing import Dict, Any, Optional

import httpx

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

logger = logging.getLogger(__name__)

# Default LLM server URLs per model type
LLM_SERVER_URLS = {
    "gemini": "https://gemini.google.com",
    "openai": "https://openai.com",
    "anthropic": "https://api.anthropic.com",
}


@mcp_server.tool()
async def setupARIConfiguration(
    modelType: str,
    llmApiKey: str,
    modelVersion: str,
    mcpServerUrl: str = "https://mcp.insightfinder.com",
    mcpApiKey: str = "insightfinder_api-9eb2a1defb591408",
    llmServerUrl: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set up ARI (LLM) configuration by registering model settings with InsightFinder.

    This tool validates the provided model type and version against InsightFinder's
    supported models, then submits the configuration via the mcp-model-setting API.

    **When to use this tool:**
    - To configure which LLM model ARI should use
    - When setting up a new AI/LLM integration for InsightFinder ARI
    - When changing the active model or API key for ARI

    **Supported model types:** OpenAI, Anthropic, Gemini

    **Default LLM server URLs (auto-selected by modelType):**
    - Gemini    → https://gemini.google.com
    - OpenAI    → https://openai.com
    - Anthropic → https://api.anthropic.com

    Args:
        modelType: LLM provider type. Must be one of: OpenAI, Anthropic, Gemini (required)
        llmApiKey: API key for the LLM provider (required)
        modelVersion: Model version to use. Must be a valid version from the provider's list (required)
        mcpServerUrl: URL of the MCP server (default: https://mcp.insightfinder.com)
        mcpApiKey: API key for the MCP server (default: insightfinder_api-9eb2a1defb591408)
        llmServerUrl: URL of the LLM provider server. Auto-detected from modelType if not provided.
    Returns:
        A dictionary with:
        - status: "success" or "error"
        - message: Human-readable result message
        - configuration: The submitted configuration (on success)

    Example:
        result = await setupARIConfiguration(
            modelType="OpenAI",
            llmApiKey="sk-...",
            modelVersion="gpt-4o"
        )
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {
                "status": "error",
                "message": "No API client configured. Please configure your InsightFinder credentials."
            }

        base_url = api_client.base_url
        headers = api_client.headers  # contains X-User-Name and X-License-Key

        # --- Validate modelType ---
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/v1/mcp-model-types",
                    headers=headers,
                    timeout=15.0
                )
                response.raise_for_status()
                types_data = response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to fetch valid model types from InsightFinder: {e}"
            }

        valid_types = []
        if types_data.get("success") and isinstance(types_data.get("result"), list):
            valid_types = types_data["result"]

        matched_type = next(
            (t for t in valid_types if t.lower() == modelType.lower()), None
        )
        if not matched_type:
            return {
                "status": "error",
                "message": (
                    f"Invalid modelType '{modelType}'. "
                    f"Valid types are: {', '.join(valid_types)}"
                ),
                "validModelTypes": valid_types,
            }
        modelType = matched_type  # normalise to server's canonical casing

        # --- Resolve llmServerUrl ---
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

        # --- Validate modelVersion ---
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/v1/mcp-model-types",
                    params={"modelType": modelType},
                    headers=headers,
                    timeout=15.0
                )
                response.raise_for_status()
                versions_data = response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to fetch valid model versions from InsightFinder: {e}"
            }

        valid_versions = []
        if versions_data.get("success") and isinstance(versions_data.get("result"), list):
            for group in versions_data["result"]:
                valid_versions.extend(group.get("versions", []))

        if modelVersion not in valid_versions:
            return {
                "status": "error",
                "message": (
                    f"Invalid modelVersion '{modelVersion}' for modelType '{modelType}'. "
                    f"Valid versions are: {', '.join(valid_versions)}"
                ),
                "validVersions": valid_versions,
            }

        # --- Submit configuration ---
        form_data = {
            "mcpServerUrl": mcpServerUrl,
            "mcpApiKey": mcpApiKey,
            "modelType": modelType,
            "llmServerUrl": llmServerUrl,
            "llmApiKey": llmApiKey,
            "isCreated": "true",
            "modelVersion": modelVersion,
            "modelVersionDisplayName": modelVersion,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/v1/mcp-model-setting",
                    data=form_data,
                    headers=headers,
                    timeout=15.0
                )
                response.raise_for_status()
                result_data = response.json()
        except httpx.HTTPStatusError as e:
            return {
                "status": "error",
                "message": f"InsightFinder returned an error: {e.response.status_code} {e.response.text}",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to submit configuration to InsightFinder: {e}",
            }

        if not result_data.get("success", False):
            return {
                "status": "error",
                "message": result_data.get("message", "InsightFinder rejected the configuration."),
                "serverResponse": result_data,
            }

        return {
            "status": "success",
            "message": f"ARI configuration successfully set up. Model: {modelType} / {modelVersion}",
            "configuration": {
                "mcpServerUrl": mcpServerUrl,
                "mcpApiKey": mcpApiKey,
                "modelType": modelType,
                "llmServerUrl": llmServerUrl,
                "modelVersion": modelVersion,
            },
            "serverResponse": result_data,
        }

    except Exception as e:
        logger.error(f"Unexpected error in setupARIConfiguration: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Unexpected error: {e}",
        }
