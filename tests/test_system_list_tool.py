"""Mocked tests for get_system_list (II-25333): the user's complete system list. No network."""
import asyncio
import json

import httpx

from insightfinder_mcp_server.api_client import insightfinder_client as ifc
from insightfinder_mcp_server.server.tools import system_info_tools as sit

_REAL = httpx.AsyncClient


def _sys(name, sid, owner="bo", projects=2, key_as_string=False, tz="UTC", aw=False):
    key = {"systemName": sid, "userName": owner, "environmentName": "All"}
    return json.dumps({
        "systemDisplayName": name,
        "isAW": aw,
        "systemKey": json.dumps(key) if key_as_string else key,
        "timezone": tz,
        "projectDetailsList": json.dumps([{"projectName": f"p{i}"} for i in range(projects)]),
    })


def _backend(monkeypatch, status=200, body=None):
    """Route the real client's systemframework call to a mocked response."""
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/external/v1/systemframework"
        if status == 204:
            return httpx.Response(204)
        return httpx.Response(status, json=body or {})

    monkeypatch.setattr(ifc.httpx, "AsyncClient",
                        lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
    client = ifc.InsightFinderAPIClient("", "bo", "lk", api_url="https://if.example")
    monkeypatch.setattr(sit, "get_current_api_client", lambda: client)
    return calls


def test_returns_owned_and_shared_sorted(monkeypatch):
    _backend(monkeypatch, body={
        "ownSystemArr": [_sys("Zeta", "z1"), _sys("alpha", "a1", key_as_string=True)],
        "shareSystemArr": [_sys("Mid", "m1", owner="alice", projects=0)],
    })
    r = asyncio.run(sit.get_system_list())
    assert r["status"] == "success"
    assert [s["name"] for s in r["systems"]] == ["alpha", "Mid", "Zeta"]
    assert (r["totalCount"], r["ownedCount"], r["sharedCount"], r["unparsedCount"]) == (3, 2, 1, 0)
    assert r["systems"][1] == {"name": "Mid", "id": "m1", "owner": "alice", "type": "IT System",
                               "timezone": "UTC", "projectCount": 0, "shared": True}
    assert r["systems"][0]["projectCount"] == 2


def test_admin_gets_only_own_and_shared(monkeypatch):
    # systemframework puts every user's systems in an admin's ownSystemArr, including ones
    # also shared with the admin.
    _backend(monkeypatch, body={
        "ownSystemArr": [_sys("Mine", "m1"), _sys("Theirs", "t1", owner="carol"),
                         _sys("Shop", "s1", owner="alice")],
        "shareSystemArr": [_sys("Shop", "s1", owner="alice")],
    })
    r = asyncio.run(sit.get_system_list())
    assert [(s["name"], s["shared"]) for s in r["systems"]] == [("Mine", False), ("Shop", True)]
    assert (r["ownedCount"], r["sharedCount"]) == (1, 1)


def test_ai_models_are_included_and_typed(monkeypatch):
    _backend(monkeypatch, body={
        "ownSystemArr": [_sys("Infra", "i1"), _sys("llama-8b", "l1", aw=True)],
        "shareSystemArr": [_sys("gpt-model", "g1", owner="alice", aw=True),
                           _sys("gpt-model", "g1", owner="alice", aw=True)],
    })
    r = asyncio.run(sit.get_system_list())
    assert [(s["name"], s["type"]) for s in r["systems"]] == [
        ("gpt-model", "AI Model"), ("Infra", "IT System"), ("llama-8b", "AI Model")]
    assert (r["totalCount"], r["itSystemCount"], r["aiModelCount"]) == (3, 1, 2)


def test_same_id_different_owner_kept(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys("A", "x")],
                                "shareSystemArr": [_sys("A", "x", owner="u2")]})
    assert asyncio.run(sit.get_system_list())["totalCount"] == 2


def test_unparsed_entries_are_counted_not_hidden(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys("Good", "g1"), "{not json", json.dumps({"systemDisplayName": "NoKey"})]})
    r = asyncio.run(sit.get_system_list())
    assert r["totalCount"] == 1 and r["unparsedCount"] == 2


def test_no_systems_is_empty_success(monkeypatch):
    # The backend answers 204 (ContentNotFoundException) when the user has no systems.
    _backend(monkeypatch, status=204)
    r = asyncio.run(sit.get_system_list())
    assert r == {"status": "success", "totalCount": 0, "ownedCount": 0, "sharedCount": 0,
                 "itSystemCount": 0, "aiModelCount": 0, "unparsedCount": 0, "systems": []}


def test_large_account_not_truncated(monkeypatch):
    _backend(monkeypatch, body={
        "ownSystemArr": [_sys(f"sys-{i:04d}", f"id{i}") for i in range(600)],
        "shareSystemArr": [_sys(f"shared-{i:03d}", f"sid{i}", owner="alice") for i in range(150)],
    })
    r = asyncio.run(sit.get_system_list())
    assert r["totalCount"] == 750 and len(r["systems"]) == 750


def test_backend_error_is_reported(monkeypatch):
    _backend(monkeypatch, status=500)
    r = asyncio.run(sit.get_system_list())
    assert r["status"] == "error" and "500" in r["message"]
