"""Mocked tests for list_all_systems / list_all_systems_and_projects scopes (II-25333). No network."""
import asyncio
import json
import re

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


def test_load_user_systems_matches_mine(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    client = sit.get_current_api_client()
    r = asyncio.run(sit.load_user_systems(client))
    assert [(s["name"], s["type"], s["shared"]) for s in r["systems"]] == [
        ("alpha", "IT System", False), ("llama-8b", "AI Model", False),
        ("Shop", "IT System", True), ("Zeta", "IT System", False)]


def test_complete_list_has_a_preview_with_every_owner(monkeypatch):
    # The same display name as an IT System and an AI Model must both show, each with its owner.
    body = {"ownSystemArr": [_sys("twin", "t-it"), _sys("twin", "t-aw", aw=True), _sys("llama", "l1", aw=True)],
            "shareSystemArr": [_sys("Shop", "s1", owner="alice")]}
    _backend(monkeypatch, body=body)
    preview = run()["formatted_preview"]
    assert preview.startswith("## Your systems (4)")
    assert "3 owned by you · 1 shared with you · 2 IT Systems · 2 AI Models" in preview
    it, ai = preview.split("### AI Models (2)")
    assert "### IT Systems (2)" in it and "**twin** (owner: bo)" in it
    assert "**Shop** (owner: alice, shared with you)" in it
    assert "**twin** (owner: bo)" in ai and "**llama** (owner: bo)" in ai
    entry_lines = [l for l in preview.splitlines() if re.match(r"\d+\. ", l)]
    assert len(entry_lines) == 4 and all("(owner: " in l for l in entry_lines)


def test_paged_list_has_no_preview(monkeypatch):
    # mcp-service shows the last tool's preview verbatim; one page of many must not pose as the list.
    _backend(monkeypatch, body={"ownSystemArr": [_sys(f"s{i}", f"id{i}") for i in range(30)]})
    assert "formatted_preview" not in run(page_size=10)
    assert "formatted_preview" in run(page_size=30)


def test_preview_wording_for_other_scopes(monkeypatch):
    _backend(monkeypatch, body=ADMIN)
    assert "shared systems not included" in run(include_shared=False)["formatted_preview"]
    everyone = run(scope="all_visible")["formatted_preview"]
    assert everyone.startswith("## Systems visible to bo (6)") and "owned by other users" in everyone
    assert "**Theirs** (owner: carol)" in everyone


def test_systems_and_projects_preview_caps_project_names(monkeypatch):
    _backend(monkeypatch, body={"ownSystemArr": [_sys("Big", "b1", projects=12), _sys("One", "o1", projects=1)]})
    preview = run(sit.list_all_systems_and_projects)["formatted_preview"]
    assert "**Big** (owner: bo) · 12 projects: p0, p1" in preview and "p9 (+2 more)" in preview
    assert "p10" not in preview and "**One** (owner: bo) · 1 project: p0" in preview


def test_empty_list_preview(monkeypatch):
    _backend(monkeypatch, status=204)
    assert "No systems found." in run()["formatted_preview"]


def test_preview_neutralises_markdown_in_names(monkeypatch):
    # A shared system's name is chosen by another user and the preview is shown verbatim.
    _backend(monkeypatch, body={"shareSystemArr": [
        _sys("x](https://evil.example)\n## Admin ![i](https://evil.example/p.png) my_sys", "e1", owner="mallory")]})
    line = next(l for l in run()["formatted_preview"].splitlines() if l.startswith("1. "))
    assert "](" not in line and "![" not in line and "\n" not in line
    assert r"x\]\(https://evil.example\) \#\# Admin \!\[i\]" in line and r"my\_sys" in line
