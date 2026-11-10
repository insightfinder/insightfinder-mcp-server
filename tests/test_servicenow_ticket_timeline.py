"""get_servicenow_ticket_timeline over change (CHG) and incident (INC) tickets (II-25594)."""
import asyncio
import json

from insightfinder_mcp_server.server.tools import deployment_tools


class _Client:
    user_name = "owner"

    def __init__(self, timelines):
        self.timelines = timelines
        self.calls = []

    async def get_servicenow_ticket_timeline(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "success", "ticketNumber": kwargs["ticket_number"], "systemId": "sys1",
                "timelines": self.timelines}


def _run(monkeypatch, timelines, number):
    client = _Client(timelines)
    monkeypatch.setattr(deployment_tools, "_get_api_client", lambda: client)
    return asyncio.run(deployment_tools.get_servicenow_ticket_timeline(number, "My System"))


def _timeline(**overrides):
    tl = {"dayTimeMillis": 1, "ticketNumber": "X", "hyperLink": "https://sn/x",
          "projectName": "proj", "instanceName": "host-1", "componentName": "comp",
          "patternName": "pat", "startTimestamp": 1767268800000, "endTimestamp": 1767268800000}
    tl.update(overrides)
    return tl


def test_change_ticket_keeps_change_window(monkeypatch):
    raw = json.dumps({"number": "CHG1", "start_date": "2026-01-02 03:04:05", "sys_id": ""})
    result = _run(monkeypatch, [_timeline(eventType="deployment", source="servicenow",
                                          rawData=raw)], "CHG1")
    event = result["events"][0]
    assert event["event_time"] == "01/02/2026 03:04:05"
    assert event["source"] == "servicenow"
    assert "incident_lookup" not in event and "incident_status" not in event
    assert result["exact_values"][0]["event_type"] == "deployment"
    assert result["citations"] == [{"source_type": "servicenow", "label": "ServiceNow CHG1",
                                     "url": "https://sn/x"}]


def test_ingested_incident_returns_servicenow_record(monkeypatch):
    raw = json.dumps({"number": "INC1", "short_description": "DNS down", "state": "Resolved",
                      "work_start": "2025-12-01 00:00:00", "close_notes": "restarted"})
    result = _run(monkeypatch, [_timeline(eventType="incident", source="servicenow", rawData=raw,
                                          incidentStatus="Closed")], "INC1")
    event = result["events"][0]
    # an incident's time is when it started, not a change-window field of its record
    assert event["event_time"] == "01/01/2026 12:00:00"
    assert event["fields"]["state"] == "Resolved"
    assert event["incident_status"] == "Closed"
    assert event["incident_lookup"] == {"system_name": "sys1",
                                        "incident_timestamp": "1767268800000",
                                        "instance_name": "host-1", "pattern_name": "pat"}


def test_insightfinder_created_incident_points_to_incident_details(monkeypatch):
    result = _run(monkeypatch, [_timeline(eventType="incident", source="insightfinder",
                                          rawData="CPU high on host-1")], "INC2")
    event = result["events"][0]
    assert event["source"] == "insightfinder"
    assert event["fields"] == {"content": "CPU high on host-1"}
    assert "incident_status" not in event
    assert event["incident_lookup"]["incident_timestamp"] == "1767268800000"


def test_ticket_without_link_is_cited_by_name(monkeypatch):
    result = _run(monkeypatch, [_timeline(eventType="incident", source="insightfinder",
                                          rawData="x", hyperLink=None)], "INC3")
    assert result["citations"] == [{"source_type": "servicenow", "label": "ServiceNow INC3"}]


def test_insightfinder_page_is_cited_next_to_the_ticket(monkeypatch):
    calls = []

    async def fake_url(client, record, event_category=None, **kwargs):
        calls.append((record, event_category))
        return "https://app/ui/global/systemrootcause?x=1"

    monkeypatch.setattr(deployment_tools, "build_systemrootcause_url", fake_url)
    result = _run(monkeypatch, [_timeline(eventType="incident", source="insightfinder",
                                          rawData="x", projectOwner="admin_nbc",
                                          patternId=10000)], "INC4")
    record, category = calls[0]
    assert category == "incident"
    assert record["userName"] == "admin_nbc" and record["patternId"] == 10000
    assert result["events"][0]["ui_url"] == "https://app/ui/global/systemrootcause?x=1"
    assert result["citations"] == [
        {"source_type": "servicenow", "label": "ServiceNow INC4", "url": "https://sn/x"},
        {"source_type": "if_anomaly_detection", "label": "My System — Incidents",
         "url": "https://app/ui/global/systemrootcause?x=1"},
    ]


def test_shared_system_is_looked_up_under_its_owner(monkeypatch):
    async def fake_identity(name):
        return {"matched": True, "owner": "admin", "system_id": "hash1", "display_name": name}

    monkeypatch.setattr(deployment_tools, "resolve_system_identity", fake_identity)
    client = _Client([_timeline(eventType="incident", source="insightfinder", rawData="x")])
    monkeypatch.setattr(deployment_tools, "_get_api_client", lambda: client)
    asyncio.run(deployment_tools.get_servicenow_ticket_timeline("INC5", "ARI-Test-System"))
    assert client.calls[0]["customer_name"] == "admin"
    assert client.calls[0]["system_name"] == "hash1"
