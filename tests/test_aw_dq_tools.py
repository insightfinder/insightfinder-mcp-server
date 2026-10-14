"""Mocked tests for the AW data quality tools and client methods (AW-2390). No network."""
import asyncio
import json

import httpx
import pytest

from insightfinder_mcp_server.api_client import aw_client
from insightfinder_mcp_server.server.tools import aw_labels as lab, aw_tools, aw_data_quality_tools as dq

_REAL = httpx.AsyncClient
D1, D2, D3 = 1791158400000, 1791244800000, 1791331200000  # 2026-10-05..07 owner-local midnights as UTC
START, END = 1791183600000, 1791442799999                  # real UTC (2026-10-05T07:00Z .. 2026-10-08T06:59:59.999Z)


SUBTYPES = {"Consistency": 1, "Missing Data": 2, "Data Outliers": 3, "Field Type Mismatch": 4,
            "Disqualified Data Range": 5, "Custom": 6, "Field Order Mismatch": 7, "Unexpected Field": 8, "Lost Field": 9}


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
         "recordsChecked": 149121, "ownerTimezone": "America/Los_Angeles", "subTypeMap": SUBTYPES}
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



# ---------------------------------------------------------------------------------------------- vocabulary
def test_workbench_labels_sections_and_numbers():
    assert lab.display_name("Consistency") == "Inconsistency" and lab.display_name("Missing Data") == "Missing Data"
    assert [lab.section_of_id(i) for i in (1, 2, 6, 7, 9)] == [lab.SECTION_CONSISTENCY, lab.SECTION_INTEGRITY,
                                                               lab.SECTION_INTEGRITY, lab.SECTION_SCHEMA, lab.SECTION_SCHEMA]
    assert lab.section_of_id(None, "Consistency") == lab.SECTION_CONSISTENCY  # id missing from subTypeMap
    assert lab.ratio(562, 7507) == "562 / 7,507 (7.49%)" and lab.pct(1694, 7507) == "22.57%"
    assert lab.pct(5, 100) == "5%" and lab.pct(3, 0) == "-" and lab.ratio(1, 3) == "1 / 3 (33.33%)"
    assert lab.signed(1200) == "+1,200" and lab.signed(-5) == "-5" and lab.signed_pct(0.042) == "+4.2%" and lab.signed_pct(0) == "0.0%"
    names = ["Inconsistency", "Lost Field", "Missing Data", "Consistency"]
    assert lab.ordered_types(["Lost Field", "Consistency", "Field Type Mismatch", "Missing Data"], SUBTYPES) == \
        ["Missing Data", "Field Type Mismatch", "Consistency", "Lost Field"]  # integrity, consistency, schema; by id
    assert names


@pytest.mark.parametrize("text,ids", [("integrity", [2, 3, 4, 5, 6]), ("consistency", [1]), ("Inconsistency", [1]),
                                      ("schema", [7, 8, 9]), ("Schema Check", [7, 8, 9]), ("MISSING_DATA", [2]),
                                      ("missing data", [2]), ("data_outliers", [3]), ("Field Type Mismatch", [4]),
                                      ("Disqualified Data Range", [5]), ("QUALIFIED_DATA_RANGE", [5]), ("CUSTOM", [6]),
                                      ("Lost Field", [9]), ("unexpected_field", [8])])
def test_issue_type_aliases(text, ids):
    assert lab.issue_type_ids(text) == ids


def test_issue_type_mapping_reaches_aw(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues())
    run(dq.aw_get_data_quality("dq1", issue_type="Disqualified Data Range"))
    assert aw.bodies()[0]["subTypes"] == [5]
    assert run(dq.aw_get_data_quality("dq1", issue_type="nope"))["status"] == "error"


# -------------------------------------------------------------------------------------------- time windows
NOW = 1791990000000  # Wed 2026-10-14 15:00:00 UTC
DAY = 86_400_000


def test_window_default_is_yesterday_and_today_in_whole_days():
    w = dq._window(None, None, NOW)
    assert (w.start, w.end, w.whole_days, w.is_default) == (NOW - DAY, NOW, True, True)
    assert dq._window("", "", NOW).is_default


def test_window_keywords_follow_the_workbench_presets():
    assert dq._window("today", None, NOW)[:3] == (NOW, NOW, True)
    assert dq._window("Yesterday", None, NOW)[:3] == (NOW - DAY, NOW - DAY, True)
    assert dq._window("this week", None, NOW).start == NOW - 3 * DAY  # Sunday-based week, Wednesday = 3 days in
    assert dq._window("this_month", None, NOW).start == NOW - 13 * DAY
    assert dq._window("last week", None, NOW)[:3] == (NOW - 7 * DAY, NOW - DAY, True)
    for text in ("3d", "last 3 days", "Last_3_days"):
        w = dq._window(text, None, NOW)
        assert (w.start, w.end, w.whole_days, w.is_default) == (NOW - 2 * DAY, NOW, True, False), text  # 3 buckets incl. today
    w = dq._window("3d", "2026-10-12", NOW)
    assert w.end == dq._noon("2026-10-12") and w.start == w.end - 2 * DAY


def test_window_dates_are_whole_days_and_hours_or_timestamps_are_exact():
    w = dq._window("2026-10-05", "2026-10-07", NOW)
    assert (w.start, w.end, w.whole_days) == (dq._noon("2026-10-05"), dq._noon("2026-10-07"), True)
    assert dq._noon("2026-10-05") == D1 + DAY // 2  # noon UTC lands inside that owner-local day
    w = dq._window("2026-10-05", None, NOW)
    assert (w.start, w.end) == (dq._noon("2026-10-05"), NOW)
    assert dq._window(None, "2026-10-07", NOW).start == dq._noon("2026-10-07") - DAY
    for text, hours in (("24h", 24), ("last 48 hours", 48), ("6 hours", 6)):
        w = dq._window(text, None, NOW)
        assert (w.start, w.end, w.whole_days) == (NOW - hours * 3600000, NOW, False), text
    w = dq._window(str(START), str(END), NOW)
    assert (w.start, w.end, w.whole_days) == (START, END, False)


def test_window_errors():
    for a, b in (("0d", None), ("0h", None), ("999d", None), ("2026-10-09", "2026-10-01"), ("2024-01-01", "2026-01-01"),
                 (str(END), str(START))):
        with pytest.raises(ValueError):
            dq._window(a, b, NOW)


def test_window_info_and_opening_sentence():
    w = dq._window(None, None)  # real clock: yesterday and today so far
    info = dq._window_info(w, "America/New_York")
    assert info["isDefault"] and info["includesToday"] and info["timezone"] == "America/New_York" and info["firstDay"] < info["lastDay"]
    text = dq._opening(info, 'monitor "dq1" (owner: alice)')
    assert text.startswith('Showing monitor "dq1" (owner: alice) for yesterday and today so far (')
    assert "two days, today is still in progress" in text and "America/New_York" in text and "Tell me a date or range" in text
    past = dq._opening(dq._window_info(dq._window("2026-10-05", "2026-10-07"), "America/New_York"), "x")
    assert "Oct 5 to Oct 7 (America/New_York; whole days)" in past and "Tell me" not in past
    one = dq._opening(dq._window_info(dq._window("2026-10-05", "2026-10-05"), "UTC"), "x")
    assert "for Oct 5 (UTC; whole day)" in one
    hours = dq._opening(dq._window_info(dq._window("24h", None), None), "x")
    assert "UTC" in hours and "Tell me" not in hours


# ----------------------------------------------------------------------------- the monitor report (Workbench layout)
def _chart(days, **per_day):
    """subTypeDistribution for [(day key, totalCount, {type: count})]."""
    return {str(k): {"distribution": dist, "totalCount": total} for k, total, dist in days}


def test_single_day_uses_the_tooltip_layout_with_sections(monkeypatch):
    d12 = {"Missing Data": 562, "Field Type Mismatch": 565, "Disqualified Data Range": 339, "Consistency": 1694}
    aw = Aw(monkeypatch, issues=_issues(subTypeDistribution=_chart([(D2, 7507, d12)]), totalAnomalyCount=3160,
                                        recordsChecked=7507, events=[]))
    r = run(dq.aw_get_data_quality("dq1", start_time="2026-10-06", end_time="2026-10-06", sections=["quality"]))
    b = aw.bodies()[0]
    assert b["startTime"] == dq._noon("2026-10-06") == b["endTime"] and len(aw.bodies()) == 1
    assert dict(aw.calls[-1].url.params)["wholeDays"] == "true"
    md = r["verbatim_markdown"]
    assert "Anomalous Data / Total Data, 10-06" in md
    assert "| Data Integrity | Missing Data | 562 / 7,507 | 7.49% |" in md
    assert "| Data Integrity | Field Type Mismatch | 565 / 7,507 | 7.53% |" in md
    assert "| Data Integrity | Disqualified Data Range | 339 / 7,507 | 4.52% |" in md
    assert "| Data Consistency | Inconsistency | 1,694 / 7,507 | 22.57% |" in md and "Total 3,160 items" in md
    assert md.index("Missing Data") < md.index("Inconsistency")  # integrity first, then consistency
    assert "flagged" not in md.lower() and "Consistency |" in md
    assert r["status"] == "success" and r["totalItems"] == 3160 and r["totalData"] == 7507
    assert r["countsByType"]["Inconsistency"] == 1694 and "Consistency" not in r["countsByType"]
    assert r["message"] == "3,160 items of anomalous data in 7,507 total data."
    assert r["opening"].startswith('Showing monitor "dq1" (owner: alice) for Oct 6 (America/Los_Angeles; whole day') and r["sections"] == ["quality"]


def test_several_days_use_a_matrix_and_mark_the_day_in_progress(monkeypatch):
    today = (int(__import__("time").time()) * 1000) // DAY * DAY  # UTC midnight; the tool compares with the owner's today
    days = [(today - DAY, 7507, {"Missing Data": 562, "Consistency": 1694}), (today, 429, {"Missing Data": 164})]
    Aw(monkeypatch, issues=_issues(subTypeDistribution=_chart(days), totalAnomalyCount=2420, recordsChecked=7936,
                                   ownerTimezone="UTC", events=[]), volume=_vol(ownerTimezone="UTC"))
    r = run(dq.aw_get_data_quality("dq1", sections=["quality"]))
    md = r["verbatim_markdown"]
    assert "| Day | Total data | Missing Data | Inconsistency | Total items |" in md
    assert f"| {dq._md(dq._day(today - DAY))} | 7,507 | 562 (7.49%) | 1,694 (22.57%) | 2,256 |" in md
    assert f"| {dq._md(dq._day(today))} (in progress) | 429 | 164 (38.23%) | 0 | 164 |" in md
    assert "Data Integrity: Missing Data. Data Consistency: Inconsistency." in md and "more than the total" in md
    assert r["window"]["isDefault"] and r["opening"].startswith("Showing monitor") and "yesterday and today so far" in r["opening"]


def test_report_has_quality_schema_and_volume_sections(monkeypatch):
    mon = _mon(monitorSetting={"schemaCheckEnabled": True, "schemaCheck": {"fieldOrder": True, "lostFields": False},
                               "volumeDetectionEnabled": True})
    days = [(D2, 1000, {"Missing Data": 100, "Field Order Mismatch": 40})]
    aw = Aw(monkeypatch, issues=_issues(subTypeDistribution=_chart(days), totalAnomalyCount=140, recordsChecked=1000, events=[]),
            volume=_vol(), monitors=[mon])
    r = run(dq.aw_get_data_quality("dq1", start_time="2026-10-06", end_time="2026-10-06"))
    md = r["verbatim_markdown"]
    assert len([c for c in aw.calls if "workbench" in c.url.path]) == 2  # one data quality call, one volume call
    assert "**Schema Check:** Enabled (Field order (Misordered))." in md
    assert "| Schema Check | Field Order Mismatch | 40 / 1,000 | 4% |" in md
    assert "| Data Integrity | Missing Data | 100 / 1,000 | 10% |" in md and "Total 100 items" in md  # schema is not double counted
    assert "**Volume** (mode: AI, sensitivity: MEDIUM, group: Total)" in md
    assert "| Day | Reference Volume | Baseline Volume | Actual Volume | vs Reference | vs Baseline | Level |" in md
    assert r["schemaCheck"]["enabled"] is True and r["volume"]["mode"] == "AI" and r["volume"]["warningDays"]


def test_schema_disabled_and_volume_off_and_volume_failure_do_not_break_the_report(monkeypatch):
    days = [(D2, 1000, {"Missing Data": 100})]
    mon = _mon(monitorSetting={"schemaCheckEnabled": False, "volumeDetectionEnabled": False})
    aw = Aw(monkeypatch, issues=_issues(subTypeDistribution=_chart(days), totalAnomalyCount=100, recordsChecked=1000, events=[]),
            monitors=[mon])
    r = run(dq.aw_get_data_quality("dq1"))
    md = r["verbatim_markdown"]
    assert "**Schema Check:** Disabled" in md and "No schema anomalies in this window." in md
    assert "Volume detection is off for this monitor" in md and r["volume"] == {"enabled": False}
    assert len([c for c in aw.calls if "workbench" in c.url.path]) == 1  # no volume call
    Aw(monkeypatch, issues=_issues(subTypeDistribution=_chart(days), totalAnomalyCount=100, recordsChecked=1000, events=[]),
       volume=httpx.Response(502, json={"message": "UIE down"}), monitors=[_mon()])
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "success" and "Volume could not be loaded" in r["verbatim_markdown"] and "UIE down" in r["volume"]["error"]
    mon = _mon(monitorSetting={"volumeDetectionEnabled": False})
    Aw(monkeypatch, issues=_issues(), monitors=[mon])
    assert run(dq.aw_get_data_quality("dq1", sections=["volume"]))["status"] == "no_data"


def test_sections_and_filters_choose_what_is_called(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues(), volume=_vol())
    run(dq.aw_get_data_quality("dq1", sections="quality, schema"))
    assert [c.url.path.endswith("volume") for c in aw.calls if "workbench" in c.url.path] == [False]
    aw = Aw(monkeypatch, issues=_issues(), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", sections=["volume"]))
    assert [c.method for c in aw.calls if "workbench" in c.url.path] == ["GET"] and r["status"] == "success"
    aw = Aw(monkeypatch, issues=_issues(), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", fields="a", issue_type="missing data"))  # a filter means quality only
    assert r["sections"] == ["quality"] and len([c for c in aw.calls if "workbench" in c.url.path]) == 1
    assert run(dq.aw_get_data_quality("dq1", sections=["nope"]))["status"] == "error"
    assert run(dq.aw_get_data_quality("dq1", sections=[]))["status"] == "error"


def test_filter_note_and_status_wording(monkeypatch):
    Aw(monkeypatch, issues=_issues(totalAnomalyCount=3252))
    r = run(dq.aw_get_data_quality("dq1", issue_type="MISSING_DATA", fields="a,b", group="g1", day="2026-10-06"))
    assert r["filtersActive"] is True and "NOT filtered" in r["note"]
    assert r["message"].startswith("3,252 items of anomalous data match your filter (issue_type=MISSING_DATA, fields=a,b, group=g1, day=2026-10-06).")
    assert "Per-day counts for the whole window (not filtered)" in r["verbatim_markdown"]
    assert r["totalData"] == 149121 and r["countsByType"]["Missing Data"] == 17  # whole window, unfiltered
    Aw(monkeypatch, issues=_issues(totalAnomalyCount=0, events=[]))
    r = run(dq.aw_get_data_quality("dq1", issue_type="CUSTOM"))
    assert r["status"] == "no_anomalous_data" and r["message"] == \
        "No anomalous data matches this filter (149,121 total data were checked in the window)."


def test_status_no_data_and_no_anomalous_data(monkeypatch):
    Aw(monkeypatch, issues=_issues(recordsChecked=0, subTypeDistribution={}, totalAnomalyCount=0, events=[]), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "no_data" and r["monitorFound"] is True and r["examples"] == []
    assert r["message"].startswith("No data was checked in this window") and "not the same as healthy" in r["message"]
    Aw(monkeypatch, issues=_issues(totalAnomalyCount=0, events=[], recordsChecked=1200, subTypeDistribution={}), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "no_anomalous_data" and "1,200 total data were checked and no anomalous data was found" in r["message"]


def test_examples_use_the_workbench_type_name(monkeypatch):
    ev = {"timestamp": D2, "target": {"info": [{"key": "k", "value": "v", "subTypeStr": "Consistency"}]}}
    Aw(monkeypatch, issues=_issues(events=[ev]), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["examples"][0]["type"] == "Inconsistency"
    assert "| Time | Data Quality Issue Type | Field | Value | Correct value | Group |" in r["verbatim_markdown"]


def test_not_found_suggests_closest_names_and_ambiguous_and_owner(monkeypatch):
    Aw(monkeypatch, issues=_issues(), monitors=[_mon("DataQuality-Demo-Monitor"), _mon("testbin0910")])
    r = run(dq.aw_get_data_quality("DataQuality-Demo-Monitr"))
    assert r["status"] == "not_found" and r["closestNames"] == ["DataQuality-Demo-Monitor"] and "Closest names" in r["message"]
    Aw(monkeypatch, issues=_issues(), monitors=[])
    assert run(dq.aw_get_data_quality("dq1"))["status"] == "not_found"
    aw = Aw(monkeypatch, issues=_issues(), monitors=[_mon(owner="alice"), _mon(owner="bob")])
    r = run(dq.aw_get_data_quality("dq1"))
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2 and not aw.bodies()
    run(dq.aw_get_data_quality("dq1", owner="bob", sections=["quality"]))
    assert aw.bodies()[0]["userName"] == "bob"


def test_bad_inputs(monkeypatch):
    Aw(monkeypatch, issues=_issues())
    for kw in ({"issue_type": "nope"}, {"page": 0}, {"page_size": 51}, {"page_size": 0}, {"day": "tomorrow"},
               {"start_time": "2026-10-09", "end_time": "2026-10-01"}, {"start_time": "2024-01-01", "end_time": "2026-01-01"}):
        assert run(dq.aw_get_data_quality("dq1", **kw))["status"] == "error", kw
    assert run(dq.aw_get_data_quality(""))["status"] == "error"


def test_day_drilldown_and_filters_reach_aw(monkeypatch):
    aw = Aw(monkeypatch, issues=_issues())
    run(dq.aw_get_data_quality("dq1", start_time="2026-10-05", end_time="2026-10-07", day="2026-10-06", fields="a, b",
                               group="g1", data_source="Logs", page=2))
    b = aw.bodies()[0]
    assert (b["startTime"], b["endTime"]) == (dq._noon("2026-10-05"), dq._noon("2026-10-07"))
    assert b["selectedDate"] == D2 and b["fields"] == ["a", "b"] and b["groups"] == ["g1"] and b["pageNo"] == 2
    assert b["dataSource"] == "Logs"
    run(dq.aw_get_data_quality("dq1", start_time="2026-10-05", end_time="2026-10-05", day=str(D2)))
    assert aw.bodies()[1]["selectedDate"] == D2  # a day key is sent back verbatim


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
    keys = {str(D1 + i * DAY): {"distribution": {"Missing Data": 1}, "totalCount": 10} for i in range(40)}
    events = [{"timestamp": D1, "target": {"info": [{"key": f"f{i}", "value": "v" * 500, "subTypeStr": "Custom"}
                                                    for i in range(120)]}} for _ in range(12)]
    Aw(monkeypatch, issues=_issues(subTypeDistribution=keys, events=events, fields=[f"f{i}" for i in range(100)],
                                   groups=[f"g{i}" for i in range(40)], totalAnomalyCount=40, recordsChecked=400),
       volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", page_size=50))
    assert len(r["daily"]) == 31 and r["daily_truncated"] and r["daily"][-1]["day"] == "2026-11-13"  # latest 31 kept
    assert len(r["examples"]) == 100 and len(r["topFields"]) == 10 and len(r["examples"][0]["value"]) == 80
    assert r["examplesNote"].startswith("and 1340 more")  # 12 * 120 - 100 shown
    assert len(r["fieldsAvailable"]) == 30 and r["total_fields_available"] == 100 and len(r["groupsAvailable"]) == 30
    assert r["truncated"] and len(json.dumps(r)) < 100000 and "Showing the latest 31 of 40 days." in r["verbatim_markdown"]
    assert not any(isinstance(r.get(k), list) and k in ("data", "items", "results") for k in r)


def test_event_paging_shows_all_rows_with_caps(monkeypatch):
    def _ev(n):
        return {"timestamp": D2, "target": {"info": [{"key": f"f{i}", "value": f"v{i}", "subTypeStr": "Custom"} for i in range(n)]}}
    Aw(monkeypatch, issues=_issues(events=[_ev(3), _ev(2)], totalAnomalyCount=9), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", page_size=2))
    assert len(r["examples"]) == 5 and "examplesNote" not in r and "more issue rows" not in r["verbatim_markdown"]
    assert r["paging"]["hasMore"] is True and r["paging"]["unit"] == "events" and r["paging"]["totalItems"] == 9
    r = run(dq.aw_get_data_quality("dq1", page_size=3, page=3))
    assert r["paging"]["hasMore"] is False  # 2 events < page_size
    Aw(monkeypatch, issues=_issues(events=[_ev(15)] * 8, totalAnomalyCount=9), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", page_size=8))
    assert len(r["examples"]) == 80 and r["examplesNote"].startswith("and 40 more") and r["truncated"]
    Aw(monkeypatch, issues=_issues(events=[_ev(10)] * 12, totalAnomalyCount=9), volume=_vol())
    r = run(dq.aw_get_data_quality("dq1", page_size=12))
    assert len(r["examples"]) == 100 and r["examplesNote"].startswith("and 20 more")


# ------------------------------------------------------------------------------------------------------- volume
def _vol(**over):
    pt = lambda ts, actual, level="normal", fin=True, ref=None, **kw: {
        "timestamp": ts, "reference": ref, "baseline": 48000.0, "actual": actual, "deviation": 0.25, "level": level,
        "expectedSource": "baseline", "finalized": fin, **kw}
    d = {"enabled": True, "detectionMode": "AI", "sensitivity": "MEDIUM", "warningRatio": 0.2, "criticalRatio": 0.4,
         "thresholdsEnabled": False, "groups": ["Total", "All"], "ownerTimezone": "Asia/Kolkata", "monitorFound": True,
         "points": [pt(D3, 100.0, "critical", False), pt(D1, 48210.0, ref=48000.0, referenceDeviation=0.0044),
                    pt(D2, 20000.0, "warning")]}
    return {**d, **over}


def test_volume_uses_the_workbench_legend_and_in_processing(monkeypatch):
    aw = Aw(monkeypatch, volume=_vol())
    r = run(dq.aw_get_data_quality_volume("dq1", start_time="2026-10-05", end_time="2026-10-07", group="Total"))
    p = dict(aw.calls[-1].url.params)
    assert p["startTime"] == str(dq._noon("2026-10-05")) and p["endTime"] == str(dq._noon("2026-10-07")) and p["wholeDays"] == "true"
    assert r["status"] == "success" and [d["day"] for d in r["days"]] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert r["warningDays"] == ["2026-10-06"] and r["criticalDays"] == [] and r["inProcessingDays"] == ["2026-10-07"]  # a day in processing has no final level
    assert r["mode"] == "AI" and r["sensitivity"] == "MEDIUM" and r["thresholds"]["warningRatio"] == 0.2
    md = r["verbatim_markdown"]
    assert "| Day | Reference Volume | Baseline Volume | Actual Volume | vs Reference | vs Baseline | Level |" in md
    assert "| 10-05 | 48,000 | 48,000 | 48,210 | +210 (+0.4%) | +210 (+0.4%) | Normal |" in md  # API deviation when present, else computed
    assert "| 10-07 | - | 48,000 | 100 | - | - | In processing |" in md
    assert "| 10-06 | - | 48,000 | 20,000 | - | -28,000 (-58.3%) | Warning |" in md  # no reference: "-"
    assert r["ownerTimezone"] == "Asia/Kolkata" and r["opening"].startswith('Showing monitor "dq1"')
    assert r["days"][0]["level"] == "normal" and r["days"][2]["level"] == "in_processing"


def test_volume_no_data_off_and_cap(monkeypatch):
    Aw(monkeypatch, volume=_vol(points=[{"timestamp": D1, "actual": None, "level": "normal", "finalized": True}]))
    assert run(dq.aw_get_data_quality_volume("dq1"))["status"] == "no_data"
    many = [{"timestamp": D1 + i * DAY, "actual": 1.0, "level": "normal", "finalized": True} for i in range(40)]
    Aw(monkeypatch, volume=_vol(points=many, enabled=False))
    r = run(dq.aw_get_data_quality_volume("dq1"))
    assert len(r["days"]) == 31 and r["days_truncated"] and "off" in r["message"]


def test_worst_level_for_the_overview_column():
    row = lambda day, level, actual=10.0, baseline=10.0, reference=None: {
        "day": day, "level": level, "actual": actual, "baseline": baseline, "reference": reference}
    rows = [row("2026-10-12", "warning"), row("2026-10-13", "critical")]
    assert dq._worst_level(rows) == "Critical on 10-13"
    assert dq._worst_level([row("2026-10-12", "normal")]) == "Normal"
    assert dq._worst_level([row("2026-10-13", "in_processing")]) == "In processing" and dq._worst_level([]) == "-"
    assert dq._worst_level([row("2026-10-12", "normal", 0.0, 0.0, 0.0), row("2026-10-13", "normal", None, None)]) == "-"  # no counts at all
    assert dq._worst_level([row("2026-10-12", "critical", 0.0, 500.0)]) == "Critical on 10-12, below baseline"  # volume stopped
    assert dq._worst_level([row("2026-10-12", "warning", 900.0, 500.0)]) == "Warning on 10-12, above baseline"


# ---------------------------------------------------------------------------------------------------- overview
def _listing(n, **setting):
    return {"scope": "organization", "totalElements": n,
            "monitors": [_mon(f"mon{i:02d}", f"o{i % 2}", monitorSetting=dict(setting)) for i in range(n)]}


def test_overview_one_table_for_quality_schema_and_volume(monkeypatch):
    totals = {"mon00": 5, "mon01": 50, "mon02": 0, "mon03": None, "mon04": 20}

    def issues(request):
        name = json.loads(request.content)["monitorName"]
        t = totals[name]
        if t is None:
            return httpx.Response(502, json={"message": "UIE down"})
        dist = {"Missing Data": t} if t else {}
        dist.update({"Lost Field": 7} if name == "mon01" else {})
        return {"subTypeDistribution": {str(D1): {"distribution": dist, "totalCount": 100}}, "totalAnomalyCount": t,
                "recordsChecked": 100, "events": [], "ownerTimezone": "UTC", "subTypeMap": SUBTYPES}

    listing = _listing(7, schemaCheckEnabled=True, volumeDetectionEnabled=True)
    listing["monitors"][2]["monitorSetting"] = {"schemaCheckEnabled": False, "volumeDetectionEnabled": False}
    aw = Aw(monkeypatch, issues=issues, listing=listing, volume=_vol())
    r = run(dq.aw_get_data_quality_overview(max_monitors=5))
    assert [m["monitorName"] for m in r["monitors"]] == ["mon01", "mon04", "mon00", "mon02", "mon03"]  # ranked, error last
    top = r["monitors"][0]
    assert top["totalItems"] == 50 and top["topType"] == "Missing Data" and top["schemaItems"] == 7  # schema is not in the 50
    assert top["schemaCheck"] == "Enabled, 7 / 100 (7%)" and top["volume"] == "Warning on 10-06, below baseline"  # worst final level, with its direction
    assert r["monitors"][3]["schemaCheck"] == "Disabled" and r["monitors"][3]["volume"] == "Off"
    assert r["monitors"][-1]["status"] == "error" and "502" in r["monitors"][-1]["message"]
    assert r["status"] == "success" and r["notShown"] == 2 and r["monitorsTotal"] == 7 and r["scope"] == "organization"
    assert all(b["pageNo"] == 1 and b["pageSize"] == 1 for b in aw.bodies()) and len(aw.bodies()) == 5
    md = r["verbatim_markdown"]
    assert "| Monitor | Model | Owner | Anomalous Data / Total Data | Top type | Schema Check | Volume |" in md
    assert "| mon01 | m | o1 | 50 / 100 (50%) | Missing Data | Enabled, 7 / 100 (7%) | Warning on 10-06, below baseline |" in md
    assert "2 more not shown" in md and "error: " in md and "UIE down" in md and "flagged" not in md.lower()
    assert "window: yesterday and today so far:" in md and "whole days, today in progress" in md  # shown even if the model skips the opening
    assert "4 monitors had data checked and 0 had no data in this window, 1 failed." in r["message"]
    assert r["opening"].startswith("Showing all data quality monitors in the organization (5 of 7 queried) for yesterday and today so far")
    assert 'Name a monitor (for example "mon01")' in r["opening"]


def test_overview_model_filter_and_volume_calls_skipped_when_off(monkeypatch):
    listing = _listing(4, volumeDetectionEnabled=False)
    for i, m in enumerate(listing["monitors"]):
        m["modelName"] = "A" if i < 3 else "B"
    aw = Aw(monkeypatch, issues=_issues(), listing=listing)
    r = run(dq.aw_get_data_quality_overview(model_name="a"))
    assert [m["monitorName"] for m in r["monitors"]] == ["mon00", "mon01", "mon02"] and r["monitorsTotal"] == 3
    assert not [c for c in aw.calls if c.url.path.endswith("volume")]
    assert "monitors of model a" in r["opening"]
    assert run(dq.aw_get_data_quality_overview(model_name="zzz"))["status"] == "not_found"


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

        async def get_data_quality_volume(self, *a, **k):
            return {"status": "success", "data": {"points": []}}

    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "https://aw.example")
    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: Fake())
    r = run(dq.aw_get_data_quality_overview(max_monitors=12))
    assert state["peak"] == 5 and r["status"] == "no_anomalous_data"


def test_overview_no_data_everywhere(monkeypatch):
    Aw(monkeypatch, issues=_issues(recordsChecked=0, subTypeDistribution={}, totalAnomalyCount=0), listing=_listing(2), volume=_vol())
    r = run(dq.aw_get_data_quality_overview())
    assert r["status"] == "no_data" and "| no data |" in r["verbatim_markdown"] and r["message"].startswith("No data was checked")


def test_overview_empty_errors_and_validation(monkeypatch):
    Aw(monkeypatch, listing={"monitors": [], "totalElements": 0})
    assert run(dq.aw_get_data_quality_overview())["status"] == "not_found"
    Aw(monkeypatch, listing=httpx.Response(403, json={"message": "x"}))
    assert run(dq.aw_get_data_quality_overview(scope="mine"))["status"] == "error"
    assert run(dq.aw_get_data_quality_overview(scope="team"))["status"] == "error"
    assert run(dq.aw_get_data_quality_overview(max_monitors=0))["status"] == "error"
    assert run(dq.aw_get_data_quality_overview(start_time="999d"))["status"] == "error"


def test_tools_need_aw_api_url(monkeypatch):
    monkeypatch.setattr(aw_client.settings, "AW_API_URL", "")
    for r in (run(dq.aw_get_data_quality("x")), run(dq.aw_get_data_quality_volume("x")),
              run(dq.aw_get_data_quality_overview())):
        assert r["status"] == "error" and "AW_API_URL" in r["message"]


def test_data_source_valid_case_insensitive_invalid_and_echo(monkeypatch):
    mons = [_mon(dataSources=[{"name": "Trace", "dataSource": "x"}, {"name": "apiLogs"}])]
    aw = Aw(monkeypatch, issues=_issues(), volume=_vol(), monitors=mons)
    r = run(dq.aw_get_data_quality("dq1", data_source="APILOGS", sections=["quality"]))
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


def test_hostile_values_are_neutralized(monkeypatch):
    bad = "a\\|b ![x](https://evil/?q=1) <img src=x>\r\nnext"
    ev = {"timestamp": D2, "target": {"info": [{"key": bad, "value": "z" * 300, "correctValue": bad, "groupName": bad,
                                                "subTypeStr": "Custom"}]}}
    Aw(monkeypatch, issues=_issues(events=[ev], fields=[bad], groups=[bad]), volume=_vol())
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
        assert "![" not in r["opening"] and "<img" not in r["opening"]
    Aw(monkeypatch, listing={"totalElements": 1, "monitors": [_mon(name=bad)]}, issues=_issues(), volume=_vol())
    r = run(dq.aw_get_data_quality_overview())
    assert "![" not in r["verbatim_markdown"] and "<img" not in r["verbatim_markdown"] and "\n| a\\\\\\|b" in r["verbatim_markdown"]
    assert "![" not in r["opening"] and "<img" not in r["opening"]


def test_cell_truncation_is_80():
    assert dq._cell("é" * 300, 80) == "é" * 79 + "…" and dq._cell("short", 80) == "short"


def test_resolve_401_goes_through_friendly_failure(monkeypatch):
    Aw(monkeypatch, issues=_issues(), volume=_vol())

    class Fake(aw_tools.get_current_aw_client().__class__):
        async def _request(self, *a, **k):
            return {"status": "error", "http_status": 401, "message": "AW API request failed: 401"}

    monkeypatch.setattr(aw_tools, "get_current_aw_client", lambda: Fake("https://aw.example", "alice", "bad"))
    for r in (run(dq.aw_get_data_quality("dq1")), run(dq.aw_get_data_quality_volume("dq1")),
              run(dq.aw_get_data_quality_overview())):
        assert r["status"] == "error" and r["message"].startswith("AW rejected the credentials (HTTP 401)")


def test_client_sends_whole_days_flag_only_when_asked(monkeypatch):
    from insightfinder_mcp_server.api_client.aw_client import AWAPIClient
    seen = []

    async def fake_request(self, method, path, timeout, **kw):
        seen.append((method, path, kw.get("params")))
        return {"status": "success", "data": {}}

    monkeypatch.setattr(AWAPIClient, "_request", fake_request)
    c = AWAPIClient("http://aw", "u", "k")
    run(c.get_data_quality("o", "m", 1, 2, whole_days=True))
    run(c.get_data_quality("o", "m", 1, 2))
    run(c.get_data_quality_volume("o", "m", 1, 2, whole_days=True))
    run(c.get_data_quality_volume("o", "m", 1, 2))
    assert seen[0][2] == {"wholeDays": "true"} and seen[1][2] is None
    assert seen[2][2]["wholeDays"] == "true" and "wholeDays" not in seen[3][2]


def test_every_table_block_starts_with_the_opening_sentence(monkeypatch):
    """The model's own first sentence is not guaranteed, so the appended block always leads with the window."""
    Aw(monkeypatch, issues=_issues(), volume=_vol(), listing=_listing(2))
    for r in (run(dq.aw_get_data_quality("dq1")), run(dq.aw_get_data_quality_volume("dq1")),
              run(dq.aw_get_data_quality_overview())):
        md = r["verbatim_markdown"]
        assert md.startswith("*Showing ") and r["opening"] in md.split("\n")[0]
        assert "yesterday and today so far" in md.split("\n")[0] and "Tell me a date or range" in md.split("\n")[0]
