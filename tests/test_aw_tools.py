"""Mocked tests for the monitor lookup and its cache (AW-2390 round 4). No network."""
import asyncio

import pytest

from insightfinder_mcp_server.server.tools import aw_tools

run = asyncio.run


def _m(name, owner="alice", **kw):
    return {"monitorName": name, "userName": owner, "monitorType": "DATA_QUALITY", "modelName": "m", **kw}


class Fake:
    """A client whose monitor list is counted and can change."""
    base_url, user_name, headers = "https://aw.example", "alice", {"X-Api-Key": "k1"}

    def __init__(self, items):
        self.items, self.calls = items, 0

    async def list_monitors(self, name, kind, page, size):
        self.calls += 1
        return {"status": "success", "data": {"content": self.items, "totalPages": 1}}


def test_the_monitor_list_is_fetched_once_per_minute(monkeypatch):
    c = Fake([_m("a")])
    first = run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))
    c.items = [_m("a"), _m("b")]
    second = run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))
    assert c.calls == 1 and second is first and len(second["items"]) == 1   # cached
    run(aw_tools._fetch_all_monitors(c, "a", "DATA_QUALITY"))               # another question is another list
    assert c.calls == 2
    clock = {"t": 1000.0}
    monkeypatch.setattr(aw_tools.time, "monotonic", lambda: clock["t"])
    aw_tools.clear_monitor_cache()
    run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))
    clock["t"] += aw_tools.MONITOR_CACHE_TTL + 1
    assert len(run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))["items"]) == 2  # expired: fetched again


def test_a_cached_list_never_crosses_users():
    a, b = Fake([_m("mine")]), Fake([_m("theirs")])
    b.headers = {"X-Api-Key": "k2"}
    run(aw_tools._fetch_all_monitors(a, None, "DATA_QUALITY"))
    assert run(aw_tools._fetch_all_monitors(b, None, "DATA_QUALITY"))["items"][0]["monitorName"] == "theirs"


def test_a_failed_fetch_is_not_cached():
    class Down(Fake):
        async def list_monitors(self, *a):
            self.calls += 1
            return {"status": "error", "message": "down"}
    c = Down([])
    run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))
    run(aw_tools._fetch_all_monitors(c, None, "DATA_QUALITY"))
    assert c.calls == 2


def test_resolver_makes_no_extra_api_call():
    c = Fake([_m("mon-a", "ting", modelName="X"), _m("mon-b", "ting", modelName="Y"), _m("solo", "rajat", modelName="Z")])
    m, failure = run(aw_tools._resolve_dq_monitor(c, "RAJAT", None))
    assert failure is None and m["monitorName"] == "solo" and "was read as owner" in m["_interpreted"] and c.calls == 1
    m, failure = run(aw_tools._resolve_dq_monitor(c, "ting", None))
    assert m is None and failure["status"] == "multi_match" and failure["matchedBy"] == "owner" and c.calls == 1
    assert failure["candidates"] == [{"monitorName": "mon-a", "userName": "ting"}, {"monitorName": "mon-b", "userName": "ting"}]
    m, failure = run(aw_tools._resolve_dq_monitor(c, "q", None))      # one character: not read as anything
    assert failure["status"] == "not_found" and c.calls == 1


def test_exact_names_still_win_and_same_name_two_owners_is_ambiguous():
    c = Fake([_m("dq1", "alice"), _m("dq1", "bob"), _m("dq10", "carol")])
    m, failure = run(aw_tools._resolve_dq_monitor(c, "dq10", None))
    assert failure is None and m["monitorName"] == "dq10" and "_interpreted" not in m   # exact: not "interpreted"
    m, failure = run(aw_tools._resolve_dq_monitor(c, "DQ1", None))                      # same name, other case, two owners
    assert failure["status"] == "ambiguous" and len(failure["candidates"]) == 2
    m, failure = run(aw_tools._resolve_dq_monitor(c, "dq1", "bob"))
    assert m["userName"] == "bob"
