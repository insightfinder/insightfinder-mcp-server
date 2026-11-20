"""Mocked tests for setupARIConfiguration with the "Custom" model type (II-25496). No network.

Custom is the user's own OpenAI-compatible server: its model name is not in any version list, its
server URL has no default, and its key may be empty.
"""
import asyncio

import pytest

from insightfinder_mcp_server.server.tools import ari_configuration_tools as ari

run = asyncio.run

TYPES = ["OpenAI", "Anthropic", "Gemini", "AWS Bedrock", "Azure Foundry", "Custom"]


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeHttp:
    """Answers the four UIE endpoints the tool calls and records every request."""
    calls = []
    saved = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        _FakeHttp.calls.append(("GET", url, params))
        if url.endswith("/mcp-model-types"):
            return _Resp({"success": True, "result": TYPES})
        if url.endswith("/mcp-model-versions"):
            return _Resp({"success": True, "result": [
                {"title": "Default Models", "versions": ["gpt-4.1"]}]})
        if url.endswith("/mcp-model-setting"):
            return _Resp({"success": True, "settings": _FakeHttp.saved})
        raise AssertionError(url)

    async def post(self, url, data=None, headers=None, timeout=None):
        _FakeHttp.calls.append(("POST", url, data))
        _FakeHttp.saved.append({"modelType": data["modelType"],
                                "modelVersion": data["modelVersion"]})
        return _Resp({"success": True})


class _Client:
    base_url = "https://uie.example"
    headers = {"X-Api-Key": "k"}


@pytest.fixture(autouse=True)
def _fake_uie(monkeypatch):
    _FakeHttp.calls = []
    _FakeHttp.saved = []
    monkeypatch.setattr(ari.httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr(ari, "get_current_api_client", lambda: _Client())


def _setup(**kw):
    fn = getattr(ari.setupARIConfiguration, "fn", ari.setupARIConfiguration)
    args = dict(modelType="Custom", llmApiKey="", modelVersion="meta-llama/Llama-3.1-8B-Instruct",
                mcpApiKey="mk", llmServerUrl="https://llm.example.com/v1")
    args.update(kw)
    return run(fn(**args))


def _posted():
    return [c[2] for c in _FakeHttp.calls if c[0] == "POST"]


def test_custom_saves_any_model_name_without_a_version_list():
    result = _setup()
    assert result["status"] == "success", result
    assert not any(c[1].endswith("/mcp-model-versions") for c in _FakeHttp.calls)
    form = _posted()[0]
    assert form["modelType"] == "Custom"
    assert form["modelVersion"] == "meta-llama/Llama-3.1-8B-Instruct"
    assert form["llmServerUrl"] == "https://llm.example.com/v1"
    assert form["llmApiKey"] == ""


def test_custom_type_is_matched_case_insensitively():
    assert _setup(modelType="custom")["status"] == "success"
    assert _posted()[0]["modelType"] == "Custom"


def test_custom_without_server_url_is_refused_before_saving():
    result = _setup(llmServerUrl=None)
    assert result["status"] == "error"
    assert "llmServerUrl is required for Custom" in result["message"]
    assert _posted() == []


def test_custom_without_model_name_is_refused_before_saving():
    result = _setup(modelVersion="")
    assert result["status"] == "error"
    assert _posted() == []


def test_other_types_still_check_the_version_list():
    result = _setup(modelType="OpenAI", modelVersion="my-own-model", llmApiKey="sk")
    assert result["status"] == "error"
    assert "Invalid modelVersion" in result["message"]
    assert _posted() == []
