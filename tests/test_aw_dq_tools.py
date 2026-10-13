"""Mocked tests for the AW data quality tools and client methods (AW-2390). No network."""
import asyncio
import json

import httpx
import pytest

from insightfinder_mcp_server.api_client import aw_client
from insightfinder_mcp_server.server.tools import aw_tools, aw_data_quality_tools as dq

_REAL = httpx.AsyncClient
D1, D2, D3 = 1791158400000, 1791244800000, 1791331200000  # 2026-10-05..07 owner-local midnights as UTC
START, END = 1791183600000, 1791442799999                  # real UTC (2026-10-05T07:00Z .. 2026-10-08T06:59:59.999Z)


def _mon(name="dq1", owner="alice", **kw):
    return {"monitorName": name, "userName": owner, "monitorType": "DATA_QUALITY", "modelName": "m", "version": "V1",
            "dataSources": [{"name": "Logs"}], "monitorSetting": {}, **kw}


def _issues(**over):
    ev = {"timestamp": D2 + 3600000, "dataQualityType": "Missing Data", "target": {"info": [
        {"anomalyType": "Missing Data", "key": "device_id", "value": "", "correctValue": "x|y", "subTypeStr": "Missing Data",
         "groupName": "All"}]}}
    d = {"subTypeDistribution": {str(D1): {"distribution": {"Missing Data": 12, "Data Outliers": 3}, "totalCount": 48210},
                                 str(D2): {"distribution": {"Missing Data": 5}, "totalCount": 51034},
                                 str(D3): {"distribution": {}, "totalCount": 49877}},
         "events": [ev], "totalAnomalyCount": 20, "fields": ["b", "a"], "groups": ["All"], "monitorFound": True,
         "recordsChecked": 149121, "ownerTimezone": "America/Los_Angeles"}
    return {**d, **over}


class Aw:
    """Routes mocked AW calls and records them."""
    def __init__(self, monkeypatch, issues=None, volume=None, monitors=None, listing=None):
        self.calls, self.issues, self.volume, self.listing = [], issues, volume, listing
        self.monitors = [_mon()] if monitors is None else monitors
        monkeypatch.setattr(aw_client.httpx, "AsyncClient",
                            lambda **kw: _REAL(transport=httpx.MockTransport(self.handler), **kw))
        monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
        c = aw_client.AWAPIClient("https://aw.example", "alice", "k")
        monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: c)

    def handler(self, request):
        self.calls.append(request)
        p = request.url.path
        r = {"/api/external/v1/workbench/data-quality": self.issues,
             "/api/external/v1/workbench/data-quality/volume": self.volume,
             "/api/external/v1/workbench/data-quality/monitors": self.listing}.get(p)
        if p == "/api/external/v1/monitor":
            return httpx.Response(200, json={"content": self.monitors, "totalPages": 1})
        r = r(request) if callable(r) else r
        if isinstance(r, httpx.Response):
            return r
        return httpx.Response(200, json=r) if r is not None else httpx.Response(204)

    def bodies(self):
        return [json.loads(c.content) for c in self.calls if c.method == "POST"]


run = asyncio.run


def test_client_post_contract_and_timeouts(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues())
    c = aw_tools.get_current_aw_client()
    run(c.get_data_quality("alice", "dq1", START, END, 1, 10, None, {"b", "a"}, [3, 2], ["g"], "Logs"))
    b = aw.bodies()[0]
    assert b == {"monitorName": "dq1", "userName": "alice", "startTime": START, "endTime": END, "pageNo": 1,
                 "pageSize": 10, "type": 0, "fields": ["a", "b"], "subTypes": [2, 3], "groups": ["g"],
                 "dataSource": "Logs"}
    assert aw.calls[0].headers["x-user-name"] == "alice"
    assert c._dq_timeout(0, DAY := 86_400_000) == 30.0 and c._dq_timeout(0, 30 * DAY) == 120.0


def test_client_post_empty_and_errors(monkeypatch):
    for code, detail in ((403, "no"), (502, "UIE down")):
        Aw(monkeypatch, issues=httpx.Response(code, json={"message": detail}))
        r = run(aw_tools.get_current_aw_client().get_data_quality("a", "m", 1, 2))
        assert r["http_status"] == code and r["detail"] == detail
    Aw(monkeypatch)
    assert run(aw_tools.get_current_aw_client().get_data_quality("a", "m", 1, 2)) == {"status": "empty"}


def test_client_volume_and_monitors_params(monkeypatch):
    aw = Aw(monkeypatch, volume={"points": []}, listing={"monitors": []})
    c = aw_tools.get_current_aw_client()
    run(c.get_data_quality_volume("alice", "dq1", START, END, group="g1"))
    run(c.list_data_quality_monitors("mine"))
    assert dict(aw.calls[0].url.params) == {"userName": "alice", "monitorName": "dq1", "startTime": str(START),
                                            "endTime": str(END), "group": "g1"}
    assert dict(aw.calls[1].url.params) == {"scope": "mine", "pageNumber": "0", "pageSize": "500"}


def test_issues_success_digest_utc_times_and_table(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues())
    r = run(dq.aw_get_data_quality("dq1", start_time=str(START), end_time=str(END), issue_type="integrity"))
    b = aw.bodies()[0]
    assert (b["startTime"], b["endTime"]) == (START, END)  # real UTC, no second timezone shift
    assert b["pageNo"] == 1 and b["subTypes"] == list(range(2, 10)) and b["userName"] == "alice" and "selectedDate" not in b
    assert r["status"] == "success" and r["totalFlaggedEntries"] == 20 and r["recordsChecked"] == 149121
    assert [d["day"] for d in r["daily"]] == ["2026-10-05", "2026-10-06", "2026-10-07"]  # plain UTC formatting of keys
    assert r["countsByRuleType"] == {"Missing Data": 17, "Data Outliers": 3} and r["ownerTimezone"] == "America/Los_Angeles"
    assert r["examples"] == [{"time": "2026-10-06 01:00:00", "rule": "Missing Data", "field": "device_id", "value": "",
                              "correctValue": "x\\|y", "group": "All"}]
    assert r["fieldsAvailable"] == ["a", "b"] and r["topFields"][0]["field"] == "device_id"
    assert r["paging"] == {"page": 1, "pageSize": 10, "unit": "events",
                          "totalFlaggedEntries": 20, "totalPages": 2, "hasMore": False}  # 1 event < page_size 10
    md = r["verbatim_markdown"]
    assert "| 2026-10-06 | 51034 | 5 |" in md and "x\\|y" in md and "flagged entries" in md and "bad rows" not in md
    assert "alice" in md and "America/Los_Angeles" in md


@pytest.mark.parametrize("issue,ids", [("consistency", [1]), ("schema", [7, 8, 9]), ("MISSING_DATA", [2]),
                                       ("data_outliers", [3]), ("FIELD_TYPE_MISMATCH", [4]),
                                       ("QUALIFIED_DATA_RANGE", [5]), ("CUSTOM", [6])])
def test_issue_type_mapping(monkeypatch, issue, ids):
    aw = Aw(monkeypatch, issues=_issues())
    run(dq.aw_get_data_quality("dq1", issue_type=issue))
    assert aw.bodies()[0]["subTypes"] == ids


def test_bad_inputs(monkeypatch):
    Aw(monkeypatch, issues=_issues())
    for kw in ({"issue_type": "nope"}, {"page": 0}, {"page_size": 51}, {"page_size": 0}, {"day": "tomorrow"},
               {"start_time": "2026-10-09", "end_time": "2026-10-01"}, {"start_time": "2024-01-01", "end_time": "2026-01-01"}):
        assert run(dq.aw_get_data_quality("dq1", **kw))["status"] == "error", kw
    assert run(dq.aw_get_data_quality(""))["status"] == "error"


def test_date_range_end_date_is_whole_day_and_day_drilldown(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues())
    run(dq.aw_get_data_quality("dq1", start_time="2026-10-05", end_time="2026-10-07", day="2026-10-06", fields="a, b",
                               group="g1", data_source="Logs", page=2))
    b = aw.bodies()[0]
    assert (b["startTime"], b["endTime"]) == (D1, D3 + 86_399_999)
    assert b["selectedDate"] == D2 and b["fields"] == ["a", "b"] and b["groups"] == ["g1"] and b["pageNo"] == 2
    assert b["dataSource"] == "Logs"
    run(dq.aw_get_data_quality("dq1", start_time="2026-10-05", end_time="2026-10-05", day=str(D2)))
    assert (aw.bodies()[1]["startTime"], aw.bodies()[1]["selectedDate"]) == (D1, D2)  # same date = that day; key verbatim


def test_status_no_data_and_no_flagged(monkeypatch):
    Aw(monkeypatch, issues=_issues(recordsChecked=0, subTypeDistribution={}, totalAnomalyCount=0, events=[]))
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "no_data" and r["monitorFound"] is True and r["examples"] == []
    Aw(monkeypatch, issues=_issues(totalAnomalyCount=0, events=[], recordsChecked=1200))
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "no_flagged_entries" and "1200 records" in r["message"]


def test_not_found_ambiguous_and_owner_resolution(monkeypatch):
    Aw(monkeypatch, issues=_issues(), monitors=[])
    assert run(dq.aw_get_data_quality("dq1"))["status"] == "not_found"
    aw = Aw(monkeypatch, issues=_issues(), monitors=[_mon(owner="alice"), _mon(owner="bob")])
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2 and not aw.bodies()
    run(dq.aw_get_data_quality("dq1", owner="bob"))
    assert aw.bodies()[0]["userName"] == "bob"


@pytest.mark.parametrize("code,status,text", [(401, "error", "credentials"), (403, "error", "not allowed"),
                                              (404, "not_found", "not found"), (400, "error", "invalid"),
                                              (502, "error", "backend")])
def test_http_error_mapping(monkeypatch, code, status, text):
    Aw(monkeypatch, issues=httpx.Response(code, json={"message": "boom"}),
       volume=httpx.Response(code, json={"message": "boom"}))
    for r in (run(dq.aw_get_data_quality("dq1")), run(dq.aw_get_data_quality_volume("dq1"))):
        assert r["status"] == status and text in r["message"].lower()
    if code in (400, 502):
        assert "boom" in r["message"]


def test_caps_and_truncation(monkeypatch):
    keys = {str(D1 + i * 86_400_000): {"distribution": {"Missing Data": 1}, "totalCount": 10} for i in range(40)}
    events = [{"timestamp": D1, "target": {"info": [{"key": f"f{i}", "value": "v" * 500, "subTypeStr": "Custom"}
                                                    for i in range(120)]}} for _ in range(12)]
    Aw(monkeypatch, issues=_issues(subTypeDistribution=keys, events=events, fields=[f"f{i}" for i in range(100)],
                                   groups=[f"g{i}" for i in range(40)], totalAnomalyCount=40, recordsChecked=400))
    r = run(dq.aw_get_data_quality("dq1", page_size=50))
    assert len(r["daily"]) == 31 and r["daily_truncated"] and r["daily"][-1]["day"] == "2026-11-13"  # latest 31 kept
    assert len(r["examples"]) == 100 and len(r["topFields"]) == 10 and len(r["examples"][0]["value"]) == 80
    assert r["examplesNote"].startswith("and 1340 more")  # 12 * 120 - 100 shown
    assert len(r["fieldsAvailable"]) == 30 and r["total_fields_available"] == 100 and len(r["groupsAvailable"]) == 30
    assert r["truncated"] and len(json.dumps(r)) < 100000
    assert not any(isinstance(r.get(k), list) and k in ("data", "items", "results") for k in r)


def _vol(**over):
    pt = lambda ts, actual, level="normal", fin=True: {"timestamp": ts, "reference": None, "baseline": 48000.0,
                                                        "actual": actual, "deviation": 0.25, "level": level,
                                                        "expectedSource": "baseline", "finalized": fin}
    d = {"enabled": True, "detectionMode": "AI", "sensitivity": "MEDIUM", "warningRatio": 0.2, "criticalRatio": 0.4,
         "thresholdsEnabled": False, "groups": ["Total", "All"], "ownerTimezone": "Asia/Kolkata", "monitorFound": True,
         "points": [pt(D3, 100.0, "critical", False), pt(D1, 48210.0), pt(D2, 20000.0, "warning")]}
    return {**d, **over}


def test_volume_digest(monkeypatch):
    aw = Aw(monkeypatch, volume=_vol())
    r = run(dq.aw_get_data_quality_volume("dq1", start_time=str(START), end_time=str(END), group="Total"))
    p = dict(aw.calls[-1].url.params)
    assert p["startTime"] == str(START) and p["endTime"] == str(END) and p["userName"] == "alice"
    assert r["status"] == "success" and [d["day"] for d in r["days"]] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert r["warningDays"] == ["2026-10-06"] and r["criticalDays"] == ["2026-10-07"] and r["inProgressDays"] == ["2026-10-07"]
    assert r["mode"] == "AI" and r["sensitivity"] == "MEDIUM" and r["thresholds"]["warningRatio"] == 0.2
    assert r["ownerTimezone"] == "Asia/Kolkata" and "| 2026-10-07 | - | 48,000 | 100 | 25.0% | critical | in progress |" in r["verbatim_markdown"]


def test_volume_no_data_off_and_cap(monkeypatch):
    Aw(monkeypatch, volume=_vol(points=[{"timestamp": D1, "actual": None, "level": "normal", "finalized": True}]))
    assert run(dq.aw_get_data_quality_volume("dq1"))["status"] == "no_data"
    many = [{"timestamp": D1 + i * 86_400_000, "actual": 1.0, "level": "normal", "finalized": True} for i in range(40)]
    Aw(monkeypatch, volume=_vol(points=many, enabled=False))
    r = run(dq.aw_get_data_quality_volume("dq1"))
    assert len(r["days"]) == 31 and r["days_truncated"] and "off" in r["message"]


def _listing(n):
    return {"scope": "organization", "totalElements": n, "monitors": [_mon(f"mon{i:02d}", f"o{i % 2}") for i in range(n)]}


def test_overview_ranking_concurrency_partial_failure_and_not_shown(monkeypatch):
    totals = {"mon00": 5, "mon01": 50, "mon02": 0, "mon03": None, "mon04": 20}

    def issues(request):
        name = json.loads(request.content)["monitorName"]
        t = totals[name]
        if t is None:
            return httpx.Response(502, json={"message": "UIE down"})
        dist = {"Missing Data": t} if t else {}
        return {"subTypeDistribution": {str(D1): {"distribution": dist, "totalCount": 100}}, "totalAnomalyCount": t,
                "recordsChecked": 100, "events": [], "ownerTimezone": "UTC"}

    aw = Aw(monkeypatch, issues=issues, listing=_listing(7))
    r = run(dq.aw_get_data_quality_overview(max_monitors=5, start_time=str(START), end_time=str(END)))
    assert [m["monitorName"] for m in r["monitors"]] == ["mon01", "mon04", "mon00", "mon02", "mon03"]  # ranked, error last
    assert r["monitors"][0]["totalFlaggedEntries"] == 50 and r["monitors"][0]["topRuleType"] == "Missing Data"
    assert r["monitors"][-1]["status"] == "error" and "502" in r["monitors"][-1]["message"]
    assert r["status"] == "success" and r["notShown"] == 2 and r["monitorsTotal"] == 7 and r["scope"] == "organization"
    assert all(b["pageNo"] == 1 and b["pageSize"] == 1 for b in aw.bodies()) and len(aw.bodies()) == 5
    md = r["verbatim_markdown"]
    assert "2 more not shown" in md and "scope: organization" in md and "| mon01 | m | o1 | Logs | 50 | Missing Data |" in md
    assert "error: " in md and "UIE down" in md


def test_overview_semaphore_bounds_concurrency(monkeypatch):
    state = {"now": 0, "peak": 0}

    class Fake:
        async def list_data_quality_monitors(self, scope):
            return {"status": "success", "data": _listing(12)}

        async def get_data_quality(self, *a, **k):
            state["now"] += 1; state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1
            return {"status": "success", "data": {"recordsChecked": 10, "totalAnomalyCount": 0, "subTypeDistribution": {}}}

    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: Fake())
    r = run(dq.aw_get_data_quality_overview(max_monitors=12))
    assert state["peak"] == 5 and r["status"] == "no_flagged_entries"


def test_overview_empty_errors_and_validation(monkeypatch):
    Aw(monkeypatch, listing={"monitors": [], "totalElements": 0})
    assert run(dq.aw_get_data_quality_overview())["status"] == "not_found"
    Aw(monkeypatch, listing=httpx.Response(403, json={"message": "x"}))
    assert run(dq.aw_get_data_quality_overview(scope="mine"))["status"] == "error"
    assert run(dq.aw_get_data_quality_overview(scope="team"))["status"] == "error"
    assert run(dq.aw_get_data_quality_overview(max_monitors=0))["status"] == "error"


def test_tools_need_aw_api_url(monkeypatch):
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "")
    for r in (run(dq.aw_get_data_quality("x")), run(dq.aw_get_data_quality_volume("x")),
              run(dq.aw_get_data_quality_overview())):
        assert r["status"] == "error" and "AW_API_URL" in r["message"]


def test_relative_ranges_last_n_days_and_hours():
    from insightfinder_mcp_server.server.tools import aw_data_quality_tools as t
    for text, span in (("3d", 3 * 86400000), ("last 3 days", 3 * 86400000), ("Last_2_days", 2 * 86400000),
                       ("24h", 86400000), ("6 hours", 6 * 3600000)):
        s, e = t._range(text, None)
        assert abs((e - s) - span) < 2 and abs(e - __import__("time").time() * 1000) < 5000, text
    s, e = t._range("2026-10-05", "2026-10-06")  # real dates are unchanged
    assert e - s == 2 * 86400000 - 1
    with pytest.raises(ValueError):
        t._range("999d", None)


def _ev(n, ts=D2, **kw):
    return {"timestamp": ts, "target": {"info": [{"key": f"f{i}", "value": f"v{i}", "subTypeStr": "Custom", **kw}
                                                 for i in range(n)]}}


def test_filters_note_and_markdown_label(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues(totalAnomalyCount=3252))
    r = run(dq.aw_get_data_quality("dq1", issue_type="MISSING_DATA", fields="a,b", group="g1", day="2026-10-06"))
    assert r["filtersActive"] is True and "NOT filtered" in r["note"]
    assert r["message"].startswith("3252 flagged entries match your filter (issue_type=MISSING_DATA, fields=a,b, group=g1, day=2026-10-06).")
    assert "NOT filtered" in r["message"] and "records checked" not in r["message"]
    md = r["verbatim_markdown"]
    assert "Per-day counts for the whole window (not filtered)" in md and md.count("NOT filtered") == 1
    assert r["recordsChecked"] == 149121 and r["countsByRuleType"]["Missing Data"] == 17  # still the whole window
    Aw(monkeypatch, issues=_issues(totalAnomalyCount=0, events=[]))
    r = run(dq.aw_get_data_quality("dq1", issue_type="CUSTOM"))
    assert r["status"] == "no_flagged_entries" and r["message"] == \
        "No flagged entries match this filter (149121 records were checked in the window)."


def test_no_filter_message_unchanged(monkeypatch):
    Aw(monkeypatch, issues=_issues())
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["message"] == "20 flagged entries in 149121 records checked." and "filtersActive" not in r and "note" not in r
    assert "not filtered" not in r["verbatim_markdown"].lower() and r["dataSource"] == dq.DEFAULT_SOURCE


def test_data_source_valid_case_insensitive_invalid_and_echo(monkeypatch):
    mons = [_mon(dataSources=[{"name": "Trace", "dataSource": "x"}, {"name": "apiLogs"}])]
    aw = Aw(monkeypatch, issues=_issues(), volume=_vol(), monitors=mons)
    r = run(dq.aw_get_data_quality("dq1", data_source="APILOGS"))
    assert aw.bodies()[0]["dataSource"] == "apiLogs" and r["dataSource"] == "APILOGS"
    assert "data source: APILOGS" in r["verbatim_markdown"]
    r = run(dq.aw_get_data_quality_volume("dq1", data_source="trace"))
    assert dict(aw.calls[-1].url.params)["dataSource"] == "Trace" and r["dataSource"] == "trace"
    n = len(aw.calls)
    for r in (run(dq.aw_get_data_quality("dq1", data_source="bogus")), run(dq.aw_get_data_quality_volume("dq1", data_source="bogus"))):
        assert r["status"] == "error" and "valid data sources: Trace, apiLogs" in r["message"]
    assert not [c for c in aw.calls[n:] if "workbench" in c.url.path]  # AW data not called
    r = run(dq.aw_get_data_quality_volume("dq1"))
    assert r["dataSource"] == dq.DEFAULT_SOURCE and "default (AW uses" in r["verbatim_markdown"]


def test_event_paging_shows_all_rows_with_caps(monkeypatch):
    Aw(monkeypatch, issues=_issues(events=[_ev(3), _ev(2)], totalAnomalyCount=9))
    r = run(dq.aw_get_data_quality("dq1", page_size=2))
    assert len(r["examples"]) == 5 and "examplesNote" not in r and "more" not in r["verbatim_markdown"]
    assert r["paging"]["hasMore"] is True and r["paging"]["unit"] == "events"  # 2 events == page_size, 2 < 9
    r = run(dq.aw_get_data_quality("dq1", page_size=3, page=3))
    assert r["paging"]["hasMore"] is False  # 2 events < page_size
    Aw(monkeypatch, issues=_issues(events=[_ev(15)] * 8, totalAnomalyCount=9))
    r = run(dq.aw_get_data_quality("dq1", page_size=8))
    assert len(r["examples"]) == 80 and r["examplesNote"].startswith("and 40 more") and r["truncated"]
    assert "and 40 more" not in r["verbatim_markdown"]
    Aw(monkeypatch, issues=_issues(events=[_ev(10)] * 12, totalAnomalyCount=9))
    r = run(dq.aw_get_data_quality("dq1", page_size=12))
    assert len(r["examples"]) == 100 and r["examplesNote"].startswith("and 20 more")


def test_hostile_values_are_neutralized(monkeypatch):
    bad = "a\\|b ![x](https://evil/?q=1) <img src=x>\r\nnext"
    ev = {"timestamp": D2, "target": {"info": [{"key": bad, "value": "z" * 300, "correctValue": bad, "groupName": bad,
                                                "subTypeStr": "Custom"}]}}
    Aw(monkeypatch, issues=_issues(events=[ev], fields=[bad], groups=[bad]))
    r = run(dq.aw_get_data_quality("dq1"))
    x = r["examples"][0]
    assert len(x["value"]) == 80 and x["value"].endswith("…")
    for v in (x["field"], x["correctValue"], x["group"], r["fieldsAvailable"][0], r["groupsAvailable"][0]):
        assert v.startswith("a\\\\\\|b ! [x] (https://evil/?q=1) &lt;img src=x&gt; next"), v
        assert "\n" not in v and "\r" not in v
    assert not any(t in json.dumps(r) for t in ("![", "](", "<img"))
    assert "<" not in r["verbatim_markdown"] and "![" not in r["verbatim_markdown"]
    # monitor names in captions and results
    Aw(monkeypatch, issues=_issues(), volume=_vol(), monitors=[_mon(name=bad)])
    for r in (run(dq.aw_get_data_quality(bad)), run(dq.aw_get_data_quality_volume(bad))):
        assert "![" not in r["verbatim_markdown"] and "<img" not in r["verbatim_markdown"] and "![" not in r["monitorName"]
    Aw(monkeypatch, listing={"totalElements": 1, "monitors": [_mon(name=bad)]}, issues=_issues())
    r = run(dq.aw_get_data_quality_overview())
    assert "![" not in r["verbatim_markdown"] and "<img" not in r["verbatim_markdown"] and "\n| a\\\\\\|b" in r["verbatim_markdown"]


def test_cell_truncation_is_80():
    assert dq._cell("é" * 300, 80) == "é" * 79 + "…" and dq._cell("short", 80) == "short"


def test_relative_start_with_end_and_zero():
    day = 86400000
    s, e = dq._range("3d", "2026-10-12")  # plain end date = whole day; span counts back from it
    assert e == 1791763200000 + day - 1  # 2026-10-12 23:59:59.999
    assert e - s == 3 * day
    s, e = dq._range("24h", "2026-10-12T10:00:00")
    assert e - s == day
    for bad in ("0d", "0h", "last 0 days"):
        with pytest.raises(ValueError, match="at least 1"):
            dq._range(bad, None)
    with pytest.raises(ValueError):
        dq._range("999d", "2026-10-12")


def test_resolve_401_goes_through_friendly_failure(monkeypatch):
    Aw(monkeypatch, issues=_issues(), volume=_vol())

    class Fake(aw_tools.get_current_aw_client().__class__):
        async def _request(self, *a, **k):
            return {"status": "error", "http_status": 401, "message": "AW API request failed: 401"}

    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: Fake("https://aw.example", "alice", "bad"))
    for r in (run(dq.aw_get_data_quality("dq1")), run(dq.aw_get_data_quality_volume("dq1")),
              run(dq.aw_get_data_quality_overview())):
        assert r["status"] == "error" and r["message"].startswith("AW rejected the credentials (HTTP 401)")
