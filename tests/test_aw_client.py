"""Checks for the AW client and tools. Run: pytest tests/test_aw_client.py (no network needed)."""
import asyncio
import json

import httpx

from insightfinder_mcp_server.api_client import aw_client
from insightfinder_mcp_server.server.tools import aw_tools


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client(monkeypatch, handler):
    monkeypatch.setattr(aw_client.httpx, "AsyncClient",
                        lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return aw_client.AWAPIClient("https://aw.example/", "alice", "key123")


def test_success_sends_credentials_and_params(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["headers"] = str(request.url), request.headers
        return httpx.Response(200, json=[{"modelName": "m1"}])

    result = asyncio.run(_client(monkeypatch, handler).list_models())
    assert result == {"status": "success", "data": [{"modelName": "m1"}]}
    assert seen["url"] == "https://aw.example/api/external/v1/model/list?userName=alice"
    assert seen["headers"]["x-user-name"] == "alice"
    assert seen["headers"]["x-api-key"] == seen["headers"]["x-license-key"] == "key123"


def test_json_string_body_is_decoded(monkeypatch):
    handler = lambda request: httpx.Response(200, json=json.dumps([{"modelName": "m1"}]))
    result = asyncio.run(_client(monkeypatch, handler).list_models())
    assert result["data"] == [{"modelName": "m1"}]


def test_empty_and_error_cases(monkeypatch):
    assert asyncio.run(_client(monkeypatch, lambda r: httpx.Response(204)).list_models()) == {"status": "empty"}

    result = asyncio.run(_client(monkeypatch, lambda r: httpx.Response(401, text="no")).list_models())
    assert result["status"] == "error" and "401" in result["message"]

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    assert "timeout" in asyncio.run(_client(monkeypatch, timeout).list_models())["message"]


def test_tools_need_aw_api_url(monkeypatch):
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "")
    result = asyncio.run(aw_tools.aw_list_models())
    assert result["status"] == "error" and "AW_API_URL" in result["message"]


def test_model_tool_filters_by_name_alone_and_caps_results(monkeypatch):
    # AW's own /model filter returns [] unless name AND version are given, so the tool filters the full list
    models = [{"modelName": "KMeans-KT", "modelVersion": "V1.0.0", "userName": "x"},
              {"modelName": "kmeans-two", "modelVersion": "V2"},
              {"modelName": "Other", "modelVersion": "V1.0.0"}]
    client = _client(monkeypatch, lambda r: httpx.Response(200, json=models))
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: client)

    result = asyncio.run(aw_tools.aw_list_models(model_name="kmeans"))
    assert [m["modelName"] for m in result["models"]] == ["KMeans-KT", "kmeans-two"]
    assert "userName" not in result["models"][0] and result["truncated"] is False

    capped = asyncio.run(aw_tools.aw_list_models(limit=1))
    assert capped["total"] == 3 and capped["truncated"] is True and len(capped["models"]) == 1
    assert asyncio.run(aw_tools.aw_list_models(limit=0))["status"] == "error"


def test_summary_waits_longer_than_default(monkeypatch):
    seen = {}
    real = _REAL_ASYNC_CLIENT

    def factory(**kw):
        seen["timeout"] = kw["timeout"]
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), **kw)

    monkeypatch.setattr(aw_client.httpx, "AsyncClient", factory)
    asyncio.run(aw_client.AWAPIClient("https://aw.example", "alice", "k").get_summary(0, 0))
    assert seen["timeout"] == 120.0  # AW needs ~31 s for a month window


def test_monitor_tool_trims_and_paginates(monkeypatch):
    page = {"content": [{"monitorName": "drift1", "monitorType": "MODEL_DRIFT", "modelName": "m1",
                         "monitorSetting": {"big": "blob"}}],
            "number": 0, "size": 10, "totalPages": 1, "totalElements": 1}
    client = _client(monkeypatch, lambda r: httpx.Response(200, json=page))
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: client)

    result = asyncio.run(aw_tools.aw_list_monitors(monitor_type="model_drift"))
    assert result["monitors"] == [{"monitorName": "drift1", "monitorType": "MODEL_DRIFT", "modelName": "m1"}]
    assert result["pagination"]["totalCount"] == 1

    assert asyncio.run(aw_tools.aw_list_monitors(monitor_type="bogus"))["status"] == "error"


def test_monitor_paging_is_stable_sorted_and_shows_owner(monkeypatch):
    # AW returns organization monitors in random order, so repeated calls must still page identically
    names = [f"mon{i:02d}" for i in range(25)]
    seen = {"sizes": []}

    def handler(request):
        seen["sizes"].append(request.url.params["pageSize"])
        shuffled = names[::-1] if len(seen["sizes"]) % 2 else names[7:] + names[:7]
        content = [{"monitorName": n, "userName": "owner1", "monitorType": "DATA_QUALITY",
                    "version": "V1", "monitorSetting": {"big": "blob"}} for n in shuffled]
        return httpx.Response(200, json={"content": content, "number": 0, "size": 1000, "totalPages": 1,
                                         "totalElements": 25})

    client = _client(monkeypatch, handler)
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: client)

    pages = [asyncio.run(aw_tools.aw_list_monitors(page=p, page_size=10)) for p in range(4)]
    got = [m["monitorName"] for r in pages for m in r["monitors"]]
    assert got == names and len(set(got)) == 25            # no repeats, none missing, sorted
    assert pages[0]["monitors"][0] == {"monitorName": "mon00", "userName": "owner1",
                                       "monitorType": "DATA_QUALITY", "version": "V1"}
    assert pages[0]["pagination"] == {"currentPage": 0, "pageSize": 10, "totalPages": 3, "totalCount": 25}
    assert pages[3]["monitors"] == [] and set(seen["sizes"]) == {"1000"}


def test_monitor_fetch_dedupes_across_aw_pages_and_flags_truncation(monkeypatch):
    def handler(request):
        page = int(request.url.params["pageNumber"])
        # every AW page repeats mon00 (unstable paging) and adds one new monitor
        content = [{"monitorName": "mon00", "userName": "u"}, {"monitorName": f"mon{page + 1:02d}", "userName": "u"}]
        return httpx.Response(200, json={"content": content, "totalPages": 12, "totalElements": 24})

    client = _client(monkeypatch, handler)
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: client)

    result = asyncio.run(aw_tools.aw_list_monitors(page_size=100))
    assert result["pagination"]["totalCount"] == 11 and result["pagination"]["truncated"] is True  # 10 pages read


def test_monitor_tool_passes_through_empty_and_single_monitor(monkeypatch):
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client",
                        lambda: _client(monkeypatch, lambda r: httpx.Response(204)))
    assert asyncio.run(aw_tools.aw_list_monitors())["status"] == "empty"

    one = {"monitorName": "m", "userName": "u", "monitorType": "MODEL_DRIFT"}  # lookup by name is unpaged
    monkeypatch.setattr(aw_tools, "get_current_aw_client",
                        lambda: _client(monkeypatch, lambda r: httpx.Response(200, json=one)))
    result = asyncio.run(aw_tools.aw_list_monitors(monitor_name="m"))
    assert result["monitors"] == [one] and result["pagination"]["totalCount"] == 1


def _dq_monitor(name="dq1", owner="alice", keys=None, **setting):
    return {"monitorName": name, "userName": owner, "monitorType": "DATA_QUALITY", "modelName": "m", "version": "V1",
            "environment": "prod", "dataSources": [{"name": "Logs", "dataType": "Log", "dataSource": "Custom"}],
            "monitorSetting": {"keys": keys, **setting}}


def _dq_tool(monkeypatch, monitors, one=None):
    def handler(request):
        if request.url.params.get("monitorName"):  # individual lookup
            return httpx.Response(200, json=one) if one else httpx.Response(403, json={"message": "no"})
        return httpx.Response(200, json={"content": monitors, "totalPages": 1})

    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: _client(monkeypatch, handler))
    return lambda **kw: asyncio.run(aw_tools.aw_get_data_quality_config(**kw))


def test_dq_config_resolves_owner_ambiguous_and_not_found(monkeypatch):
    run = _dq_tool(monkeypatch, [_dq_monitor(owner="alice"), _dq_monitor(owner="bob"), _dq_monitor("other")])
    amb = run(monitor_name="dq1")
    assert amb["status"] == "ambiguous" and {c["userName"] for c in amb["candidates"]} == {"alice", "bob"}
    assert run(monitor_name="dq1", owner="bob")["scope"] == {"owner": "bob"}
    assert run(monitor_name="nope")["status"] == "not_found"
    assert run(monitor_name="dq1", owner="carol")["status"] == "not_found"  # direct lookup got 403


def test_dq_config_other_owner_fetched_directly_when_not_in_list(monkeypatch):
    run = _dq_tool(monkeypatch, [], one=_dq_monitor(owner="rajat"))
    assert run(monitor_name="dq1", owner="rajat")["owner"] == "rajat"


def test_dq_config_caps_fields_and_digests_schema_and_volume(monkeypatch):
    keys = {f"f{i}": {"fieldType": "STRING", "dataQualityTypes": ["MISSING_DATA"]} for i in range(340)}
    keys["f339"] = {"fieldType": "NUMBER", "dataQualityTypes": ["MISSING_DATA", "DATA_OUTLIERS"], "threshold": ">0"}
    mon = _dq_monitor(keys=keys, schemaCheckEnabled=True, schemaCheck={"fieldOrder": True, "lostFields": False},
                      volumeDetectionEnabled=True, volumeDetectionMode="AI", volumeSensitivity="MEDIUM_HIGH",
                      volumeThresholds={"warning": 20.0, "critical": 40.0}, groupNameKey="vendor", groupKeys={"g1": {"a": {"big": 1}}})
    r = _dq_tool(monkeypatch, [mon])(monitor_name="dq1")
    assert r["status"] == "success" and r["total_fields"] == 340 and r["truncated"] is True
    assert len(r["fields"]) == 30 and r["fields"][0]["field"] == "f339"  # richer rules first
    assert r["rule_summary"] == {"MISSING_DATA": 340, "DATA_OUTLIERS": 1} and r["has_field_rules"] is True
    assert r["schemaCheck"] == {"enabled": True, "kinds": ["Field order (Misordered)"]}
    assert r["volumeDetection"]["mode"] == "AI" and r["volumeDetection"]["volumeSensitivity"] == "MEDIUM_HIGH"
    assert r["groups"] == {"groupNameKey": "vendor", "groupKeys": {"g1": ["a"]}} and r["dataSources"][0]["name"] == "Logs"
    assert "Showing 30 of 340" in r["verbatim_markdown"] and len(json.dumps(r)) < 100000


def test_dq_config_empty_setting(monkeypatch):
    r = _dq_tool(monkeypatch, [{**_dq_monitor(), "monitorSetting": None}])(monitor_name="dq1")
    assert r["status"] == "success" and r["total_fields"] == 0 and r["has_field_rules"] is False
    assert r["schemaCheck"]["enabled"] is False and r["volumeDetection"] == {"enabled": False}


def test_http_error_with_empty_body_is_an_error_not_empty(monkeypatch):
    for code in (502, 504):
        result = asyncio.run(_client(monkeypatch, lambda r, c=code: httpx.Response(c)).list_models())
        assert result["status"] == "error" and result["http_status"] == code
    assert asyncio.run(_client(monkeypatch, lambda r: httpx.Response(200)).list_models()) == {"status": "empty"}


def test_aw_url_is_derived_from_the_insightfinder_host_unless_overridden(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "")
    assert aw_client.resolve_aw_url("https://stg.insightfinder.com") == "https://ai-stg.insightfinder.com"
    assert aw_client.resolve_aw_url("https://app.insightfinder.com/") == "https://ai.insightfinder.com"
    assert aw_client.resolve_aw_url("https://STG.insightfinder.com:443") == "https://ai-stg.insightfinder.com"
    assert aw_client.resolve_aw_url("https://other.example.com") == "" and aw_client.resolve_aw_url(None) == ""

    ctx = SimpleNamespace(base_url="https://stg.insightfinder.com", user_name="alice", license_key="k")
    monkeypatch.setattr(aw_client, "get_current_api_client", lambda: ctx)
    assert aw_client.get_current_aw_client().base_url == "https://ai-stg.insightfinder.com"

    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "http://localhost:8090")  # explicit override wins
    assert aw_client.get_current_aw_client().base_url == "http://localhost:8090"
    assert aw_client.resolve_aw_url("https://stg.insightfinder.com") == "http://localhost:8090"

    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "")
    ctx.base_url = "https://other.example.com"  # unknown host and no override: no client, tools say so
    assert aw_client.get_current_aw_client() is None
    monkeypatch.setattr(aw_tools, "get_current_aw_client", aw_client.get_current_aw_client)
    result = asyncio.run(aw_tools.aw_list_models())
    assert result["status"] == "error" and "AW_API_URL" in result["message"]
