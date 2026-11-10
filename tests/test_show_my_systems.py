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


def _backend(monkeypatch, body, status=200):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/external/v1/systemframework"
        return httpx.Response(status, json=body)

    monkeypatch.setattr(ifc.httpx, "AsyncClient",
                        lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
    client = ifc.InsightFinderAPIClient("", "bo", "lk", api_url="https://if.example")
    monkeypatch.setattr(sit, "get_current_api_client", lambda: client)
    return calls


def run():
    return asyncio.run(sit.show_my_systems())


ADMIN = {
    "ownSystemArr": [_sys("Zeta", "z1"), _sys("alpha", "a1", key_as_string=True, tz="Asia/Shanghai"),
                     _sys("Theirs", "t1", owner="carol"), _sys("Shop", "s1", owner="alice"),
                     _sys("llama-8b", "l1", aw=True, projects=0)],
    "shareSystemArr": [_sys("Shop", "s1", owner="alice", projects=0)],
}


def test_own_and_shared_only_once_each_in_one_call(monkeypatch):
    calls = _backend(monkeypatch, ADMIN)
    r = run()
    assert r["status"] == "success"
    assert len(calls) == 1
    assert r["summary"] == {"totalSystems": 4, "itSystems": 3, "aiModels": 1,
                            "ownedSystems": 3, "sharedSystems": 1}
    preview = r["formatted_preview"]
    assert "Theirs" not in preview
    assert preview.count("| Shop |") == 1


def test_preview_groups_by_type_with_owner_on_every_row(monkeypatch):
    _backend(monkeypatch, ADMIN)
    preview = run()["formatted_preview"]
    assert preview.startswith("You have **4** systems. IT Systems: 3, AI Models: 1. "
                              "Owned by you: 3, shared with you: 1.")
    it_part, ai_part = preview.split("### AI Models (1)")
    assert "### IT Systems (3)" in it_part
    assert "| Name | Owner | Time zone | Projects |" in it_part
    assert "| System |" not in preview
    rows = [l for l in it_part.splitlines() if l.startswith("| ") and not l.startswith("| Name")]
    assert rows == ["| alpha | bo (you) | Asia/Shanghai | 2 |",
                    "| Shop | alice | UTC | 0 |",
                    "| Zeta | bo (you) | UTC | 2 |"]
    assert "| llama-8b | bo (you) | UTC | 0 |" in ai_part


def test_every_system_is_listed_past_the_old_page_size(monkeypatch):
    body = {"ownSystemArr": [_sys(f"sys-{i:03d}", f"id{i}") for i in range(101)], "shareSystemArr": []}
    _backend(monkeypatch, body)
    r = run()
    assert r["summary"]["totalSystems"] == 101
    assert "sys-000" in r["formatted_preview"] and "sys-100" in r["formatted_preview"]
    assert "page" not in r["formatted_preview"].lower()


def test_names_cannot_break_the_table(monkeypatch):
    _backend(monkeypatch, {"ownSystemArr": [_sys("a|b\nc", "x1")], "shareSystemArr": []})
    assert "| a\\|b c | bo (you) | UTC | 2 |" in run()["formatted_preview"]


def test_no_systems(monkeypatch):
    _backend(monkeypatch, {"ownSystemArr": [], "shareSystemArr": []})
    r = run()
    assert r["status"] == "success"
    assert r["summary"]["totalSystems"] == 0
    assert r["formatted_preview"] == "You have no systems yet, and no systems are shared with you."


def test_backend_error_is_returned_not_rendered(monkeypatch):
    _backend(monkeypatch, {}, status=500)
    r = run()
    assert r["status"] == "error"
    assert "formatted_preview" not in r


def test_unparseable_entries_are_skipped(monkeypatch):
    _backend(monkeypatch, {"ownSystemArr": ["not json", _sys("ok", "o1")], "shareSystemArr": []})
    assert run()["summary"]["totalSystems"] == 1
