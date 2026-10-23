"""Mocked tests for list_all_systems / list_all_systems_and_projects scopes (II-25333). No network."""
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
        "projectDetailsList": json.dumps([{"projectName": f"p{i}", "userName": owner} for i in range(projects)]),
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


def run(tool=None, **kw):
    return asyncio.run((tool or sit.list_all_systems)(**kw))


def names(r):
    return [s["systemDisplayName"] for s in r["systems"]]


# An admin's systemframework: every user's systems in ownSystemArr, some also shared with them.
ADMIN = {
    "ownSystemArr": [_sys("Zeta", "z1"), _sys("alpha", "a1", key_as_string=True),
                     _sys("Theirs", "t1", owner="carol"), _sys("Shop", "s1", owner="alice"),
                     _sys("llama-8b", "l1", aw=True)],
    "shareSystemArr": [_sys("Shop", "s1", owner="alice", projects=0)],
}


def test_default_scope_is_the_signed_in_users_own_and_shared(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    r = run()
    assert r["scope"] == "mine" and r["currentUser"] == "bo"
    assert names(r) == ["alpha", "llama-8b", "Shop", "Zeta"]  # Theirs (carol) left out, sorted
    shop = next(s for s in r["systems"] if s["systemDisplayName"] == "Shop")
    assert shop == {"systemName": "s1", "systemDisplayName": "Shop", "userName": "alice",
                    "environmentName": "All", "type": "IT System", "timezone": "UTC",
                    "projectCount": 0, "isShared": True, "ownedByCurrentUser": False}
    assert [s["systemDisplayName"] for s in r["ownedSystems"]] == ["alpha", "llama-8b", "Zeta"]
    assert [s["systemDisplayName"] for s in r["sharedSystems"]] == ["Shop"]
    assert r["pagination"]["pageSize"] == 100 and r["pagination"]["hasMore"] is False
    assert (r["summary"]["totalItSystems"], r["summary"]["totalAiModels"]) == (3, 1)


def test_all_visible_scope_keeps_the_role_wide_view(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    r = run(scope="all_visible")
    assert r["scope"] == "all_visible" and r["pagination"]["pageSize"] == 20
    assert names(r) == ["Zeta", "alpha", "Theirs", "Shop", "llama-8b", "Shop"]  # as returned
    assert {s["systemDisplayName"]: s["ownedByCurrentUser"] for s in r["systems"]}["Theirs"] is False


def test_ai_models_are_typed(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    types = {s["systemDisplayName"]: s["type"] for s in run()["systems"]}
    assert types["llama-8b"] == "AI Model" and types["Zeta"] == "IT System"


def test_include_shared_false_lists_own_only(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    assert names(run(include_shared=False)) == ["alpha", "llama-8b", "Zeta"]


def test_same_id_different_owner_kept(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys("A", "x")], "shareSystemArr": [_sys("A", "x", owner="u2")]})
    assert run()["pagination"]["totalCount"] == 2


def test_unparsed_entries_are_counted_not_hidden(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys("Good", "g1"), "{not json",
                                                 json.dumps({"systemDisplayName": "NoKey"})]})
    r = run()
    assert names(r) == ["Good"] and r["summary"]["unparsedEntries"] == 2


def test_no_systems_is_empty_success(monkeypatch):
    # The backend answers 204 (ContentNotFoundException) when the user has no systems.
    _backend(monkeypatch, status=204)
    r = run()
    assert r["status"] == "success" and r["systems"] == [] and r["pagination"]["totalCount"] == 0


def test_large_own_list_pages_at_100_and_says_so(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys(f"sys-{i:04d}", f"id{i}") for i in range(150)],
                                "shareSystemArr": []})
    r = run()
    assert len(r["systems"]) == 100 and r["pagination"]["hasMore"] is True and "page=2" in r["message"]
    # mcp-service discards tool results over 100k chars of pretty-printed JSON.
    assert len(json.dumps(r, indent=2)) < 100_000
    assert len(run(page=2)["systems"]) == 50


def test_invalid_scope_is_refused(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    r = run(scope="everyone")
    assert r["status"] == "error" and r["error_code"] == "invalid_argument"


def test_backend_error_is_reported(monkeypatch):
    _backend(monkeypatch, status=500)
    r = run()
    assert r["status"] == "error" and "500" in r["message"]


def test_systems_and_projects_default_to_mine(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    r = run(sit.list_all_systems_and_projects)
    assert r["scope"] == "mine" and names(r) == ["alpha", "llama-8b", "Shop", "Zeta"]
    assert len(next(s for s in r["systems"] if s["systemDisplayName"] == "Zeta")["projects"]) == 2
    everyone = run(sit.list_all_systems_and_projects, scope="all_visible")
    assert "Theirs" in names(everyone) and everyone["summary"]["totalSystems"] == 6
    assert (r["summary"]["totalItSystems"], r["summary"]["totalAiModels"]) == (3, 1)


def test_load_user_systems_matches_mine(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    client = sit.get_current_api_client()
    r = asyncio.run(sit.load_user_systems(client))
    assert [(s["name"], s["type"], s["shared"]) for s in r["systems"]] == [
        ("alpha", "IT System", False), ("llama-8b", "AI Model", False),
        ("Shop", "IT System", True), ("Zeta", "IT System", False)]
