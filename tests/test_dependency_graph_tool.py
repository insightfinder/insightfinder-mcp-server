"""Mocked tests for get_dependency_graph (II-25333). No network."""
import asyncio
import json

import httpx

from insightfinder_mcp_server.api_client import insightfinder_client as ifc
from insightfinder_mcp_server.server.tools import dependency_tools as dep
from insightfinder_mcp_server.server.tools import system_info_tools as sit

_REAL = httpx.AsyncClient


def _sys(name, sid, owner="bo", aw=False):
    return json.dumps({"systemDisplayName": name, "isAW": aw, "timezone": "UTC",
                       "systemKey": {"systemName": sid, "userName": owner}, "projectDetailsList": "[]"})


SYSTEMS = {"ownSystemArr": [_sys("Shop", "shop1"), _sys("Twin", "tw1")],
           "shareSystemArr": [_sys("Citizen Cane", "cc1", owner="demoUser"),
                              _sys("Twin", "tw2", owner="alice")]}

META = {"instanceMetaData": json.dumps([
    {"componentName": "core server", "instanceListStr": json.dumps([{"c": "core server", "i": "cs"}])},
    {"componentName": "Jenkins", "instanceListStr": json.dumps([{"c": "Jenkins", "i": "Jenkins"}])},
    {"componentName": "Web Portal", "instanceListStr": json.dumps([{"c": "Web Portal", "i": "ip-1"},
                                                                   {"c": "Web Portal", "i": "ip-2"}])},
    {"componentName": "Checkout", "instanceListStr": "[]"}]), "projectMetaData": []}


def _e(s, t, st=3, lap=0.9, kind="c", ext=None):
    edge = {"s": {"id": s, "type": kind}, "t": {"id": t, "type": kind}, "lap": lap, "lmp": lap, "st": st}
    if ext is not None:
        edge["e"] = ext
    return edge


def _zone(zone, comp, inst=None):
    return {"relationDependencyKey": {"relationDependencyPartitionKey": {"zone": zone}},
            "componentDependencyData": comp, "instanceDependencyData": inst if inst is not None else {}}


CITIZEN = {"dependencyMap": [_zone("NO_ZONE", [_e("Checkout", "Web Portal", 3, 1.0),
                                               _e("Jenkins", "Checkout", 4, 0.5875)])],
           "metaData": META}


class Backend:
    def __init__(self, monkeypatch, candidates=None, relations=None, systems=SYSTEMS, by_system=None):
        self.calls = []
        # systemId -> causalgroup candidates response, for lookups across systems
        self.by_system = by_system or {}
        self.candidates = candidates if candidates is not None else {
            "success": True, "configuredCausalKey": "ck1", "candidates": []}
        self.relations = relations if relations is not None else CITIZEN
        self.systems = systems
        monkeypatch.setattr(ifc.httpx, "AsyncClient",
                            lambda **kw: _REAL(transport=httpx.MockTransport(self.handler), **kw))
        client = ifc.InsightFinderAPIClient("", "bo", "lk", api_url="https://if.example")
        monkeypatch.setattr(sit, "get_current_api_client", lambda: client)
        monkeypatch.setattr(dep, "get_current_api_client", lambda: client)

    def handler(self, request):
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/systemframework"):
            return httpx.Response(200, json=self.systems)
        if path.endswith("/causalgroup"):
            return httpx.Response(200, json=self.by_system.get(request.url.params.get("systemId"),
                                                               self.candidates))
        if path.endswith("/dependencyrelation"):
            return httpx.Response(200, json=self.relations)
        raise AssertionError(path)

    def params(self, suffix):
        return dict(next(r for r in self.calls if r.url.path.endswith(suffix)).url.params)


def run(**kw):
    return asyncio.run(dep.get_dependency_graph(**kw))


# ─── Success path ─────────────────────────────────────────────────────────

def test_whole_graph_matches_ui_defaults(monkeypatch):
    b = Backend(monkeypatch)
    r = run(system_name="citizen cane")
    assert r["status"] == "success" and r["graphAvailable"] is True
    assert r["system"] == {"name": "Citizen Cane", "id": "cc1", "owner": "demoUser"}
    assert r["causalGroup"] == {"key": "ck1", "name": None, "owner": "demoUser", "configured": True}
    assert [(e["source"], e["target"], e["status"]) for e in r["edges"]] == [
        ("Checkout", "Web Portal", "inferred")]
    assert r["counts"]["hiddenNotInUse"] == 1 and r["counts"]["edgesTotal"] == 2
    # As in the UI: Jenkins (only a hidden edge) is listed, a component with no relation is not.
    assert r["components"] == [{"name": "Checkout", "instanceCount": 0},
                               {"name": "Jenkins", "instanceCount": 1},
                               {"name": "Web Portal", "instanceCount": 2}]
    assert b.params("/causalgroup") == {"operation": "candidates", "customerName": "demoUser",
                                        "systemId": "cc1"}
    assert b.params("/dependencyrelation") == {"causalKey": "ck1", "customerName": "demoUser",
                                               "causalZoneName": "ALL_ZONE", "hierarchical": "true"}
    assert r["ui-url"] == "https://if.example/ui/global/ServiceMap?s=cc1&e=All&customerName=demoUser&tab=dependency"


def test_include_inactive_returns_hidden_edges(monkeypatch):
    Backend(monkeypatch)
    r = run(system_name="Citizen Cane", include_inactive=True)
    assert {e["status"] for e in r["edges"]} == {"inferred", "not_in_use"}
    assert r["counts"]["hiddenNotInUse"] == 0


def test_status_mapping_and_external(monkeypatch):
    rel = {"dependencyMap": [_zone("NO_ZONE", [_e("A", "B", 1, ext=True), _e("B", "C", 2),
                                               {"s": {"id": "C", "type": "c"}, "t": {"id": "D", "type": "c"}}])],
           "metaData": {}}
    Backend(monkeypatch, relations=rel)
    r = run(system_name="Citizen Cane")
    assert [(e["source"], e["status"], e["external"]) for e in r["edges"]] == [
        ("A", "confirmed", True), ("C", "inferred", False)]
    assert r["counts"]["hiddenIgnored"] == 1


def test_candidate_fallback_skips_correlation_groups(monkeypatch):
    b = Backend(monkeypatch, candidates={"configuredCausalKey": None, "candidates": [
        {"causalKey": "k-corr", "causalName": "A corr", "userName": "bo", "correlationGroup": True},
        {"causalKey": "k-z", "causalName": "Zed", "userName": "bo", "correlationGroup": False},
        {"causalKey": "k-b", "causalName": "beta", "userName": "carol", "correlationGroup": False}]})
    r = run(system_name="Shop")
    assert r["causalGroup"] == {"key": "k-b", "name": "beta", "owner": "carol", "configured": False}
    assert b.params("/dependencyrelation")["customerName"] == "carol"


def test_no_causal_group_is_a_success_without_graph(monkeypatch):
    b = Backend(monkeypatch, candidates={"candidates": []})
    r = run(system_name="Shop")
    assert r["status"] == "success" and r["graphAvailable"] is False and "no causal group" in r["message"]
    assert not any(c.url.path.endswith("/dependencyrelation") for c in b.calls)


def test_mixed_edges_keep_endpoint_types(monkeypatch):
    mixed = {"s": {"id": "ip-1", "type": "i"}, "t": {"id": "Checkout", "type": "c"}, "lap": 0.7, "st": 3}
    Backend(monkeypatch, relations={"dependencyMap": [_zone("NO_ZONE", [mixed])], "metaData": {}})
    edge = run(system_name="Citizen Cane")["edges"][0]
    assert (edge["sourceType"], edge["targetType"]) == ("instance", "component")


def test_zones_merge_keeping_highest_probability(monkeypatch):
    rel = {"dependencyMap": {"z1": _zone("z1", [_e("A", "B", 3, 0.4)]),
                             "z2": _zone("z2", [_e("A", "B", 1, 0.8)])}, "metaData": {}}
    Backend(monkeypatch, relations=rel)
    r = run(system_name="Citizen Cane")
    assert r["zones"] == ["z1", "z2"]
    assert r["edges"] == [{"source": "A", "target": "B", "status": "confirmed", "probability": 0.8,
                           "external": False, "zones": ["z1", "z2"]}]


def test_instance_edges_accept_both_shapes(monkeypatch):
    as_object = {"Web Portal": [_e("ip-1", "ip-2", kind="i")]}
    as_list = [{"c": "Web Portal", "edges": [_e("ip-1", "ip-2", kind="i")]}, _e("ip-3", "ip-1", kind="i")]
    for inst, expected in ((as_object, 1), (as_list, 2)):
        Backend(monkeypatch, relations={"dependencyMap": [_zone("NO_ZONE", [], inst)], "metaData": {}})
        r = run(system_name="Citizen Cane", include_instances=True)
        assert len(r["instanceEdges"]) == expected and r["counts"]["instanceEdges"] == expected
        assert all(e["sourceType"] == "instance" for e in r["instanceEdges"])
    assert "instanceEdges" not in run(system_name="Citizen Cane")


def test_component_focus_walks_hops(monkeypatch):
    rel = {"dependencyMap": [_zone("NO_ZONE", [_e("Jenkins", "Checkout"), _e("Checkout", "Web Portal"),
                                               _e("DB", "Checkout"), _e("Web Portal", "CDN")])],
           "metaData": {}}
    Backend(monkeypatch, relations=rel)
    up1 = run(system_name="Citizen Cane", component="web portal", direction="upstream")
    assert [(e["source"], e["target"]) for e in up1["edges"]] == [("Checkout", "Web Portal")]
    assert up1["focus"] == {"component": "Web Portal", "direction": "upstream", "hops": 1}
    up2 = run(system_name="Citizen Cane", component="Web Portal", direction="upstream", hops=2)
    assert {e["source"] for e in up2["edges"]} == {"Checkout", "Jenkins", "DB"}
    down = run(system_name="Citizen Cane", component="Web Portal", direction="downstream")
    assert [(e["source"], e["target"]) for e in down["edges"]] == [("Web Portal", "CDN")]
    both = run(system_name="Citizen Cane", component="Web Portal")
    assert len(both["edges"]) == 2


def test_large_graph_is_truncated_with_hint(monkeypatch):
    rel = {"dependencyMap": [_zone("NO_ZONE", [_e(f"a{i:03d}", f"b{i}") for i in range(600)])], "metaData": {}}
    Backend(monkeypatch, relations=rel)
    r = run(system_name="Citizen Cane")
    assert r["truncated"] is True and len(r["edges"]) == 150 and "component" in r["message"]
    # mcp-service discards results over 100k chars of pretty-printed JSON.
    assert len(json.dumps(r, indent=2)) < 100_000


# ─── Call contract ────────────────────────────────────────────────────────

def test_blank_system_is_refused_with_choices(monkeypatch):
    b = Backend(monkeypatch)
    r = run(system_name="  ")
    assert r["error_code"] == "missing_system" and r["missing"] == ["system_name"] and r["ask_user"]
    assert {"name": "Citizen Cane", "owner": "demoUser"} in r["available_systems"]
    assert not any(c.url.path.endswith("/causalgroup") for c in b.calls)


def test_unknown_system_is_refused_not_guessed(monkeypatch):
    b = Backend(monkeypatch)
    r = run(system_name="Citizen Can")
    assert r["status"] == "error" and r["error_code"] == "system_not_found"
    assert r["did_you_mean"] == ["Citizen Cane"] and r["ask_user"]
    assert len(r["available_systems"]) == 4
    # It was looked up as a component in every system first.
    assert r["componentSearch"] == "no system has a component with this name"
    assert sum(c.url.path.endswith("/causalgroup") for c in b.calls) == 4


NO_GROUP = {"configuredCausalKey": None, "candidates": []}


def test_component_name_points_to_its_system(monkeypatch):
    Backend(monkeypatch, by_system={"shop1": NO_GROUP, "tw1": NO_GROUP, "tw2": NO_GROUP})
    r = run(system_name="web portal")
    assert r["error_code"] == "component_in_system" and "ask_user" not in r
    assert r["retry_with"] == {"system_name": "Citizen Cane", "component": "Web Portal"}
    ok = run(**r["retry_with"], direction="upstream")
    assert [(e["source"], e["target"]) for e in ok["edges"]] == [("Checkout", "Web Portal")]


def test_component_in_twin_systems_needs_owner_in_retry(monkeypatch):
    Backend(monkeypatch, by_system={"shop1": NO_GROUP, "cc1": NO_GROUP, "tw2": NO_GROUP})
    r = run(system_name="Jenkins")
    assert r["retry_with"] == {"system_name": "Twin", "component": "Jenkins", "system_owner": "bo"}


def test_component_in_several_systems_asks_user(monkeypatch):
    Backend(monkeypatch, by_system={"shop1": NO_GROUP, "tw2": NO_GROUP})
    r = run(system_name="Checkout")
    assert r["error_code"] == "component_in_system" and r["ask_user"]
    assert {c["system"] for c in r["candidates"]} == {"Citizen Cane", "Twin"}
    assert all(c["retry_with"]["component"] == "Checkout" for c in r["candidates"])


def test_component_lookup_skipped_on_huge_accounts(monkeypatch):
    many = {"ownSystemArr": [_sys(f"s{i}", f"id{i}") for i in range(51)], "shareSystemArr": []}
    b = Backend(monkeypatch, systems=many)
    r = run(system_name="Web Portal")
    assert r["error_code"] == "system_not_found" and r["componentSearch"].startswith("skipped")
    assert not any(c.url.path.endswith("/causalgroup") for c in b.calls)


def test_ai_model_is_refused_as_not_an_it_system(monkeypatch):
    b = Backend(monkeypatch, systems={"ownSystemArr": [_sys("llama-8b", "l1", aw=True), _sys("Shop", "s1")],
                                      "shareSystemArr": []})
    r = run(system_name="LLAMA-8B")
    assert r["error_code"] == "not_an_it_system" and r["candidates"] == [{"name": "llama-8b", "owner": "bo"}]
    assert r["available_systems"] == [{"name": "Shop", "owner": "bo"}]
    assert not any(c.url.path.endswith("/causalgroup") for c in b.calls)


def test_it_system_wins_over_same_named_ai_model(monkeypatch):
    Backend(monkeypatch, systems={"ownSystemArr": [_sys("Twin", "m1", aw=True), _sys("Twin", "t1")],
                                  "shareSystemArr": []})
    assert run(system_name="Twin")["system"]["id"] == "t1"


def test_ambiguous_system_needs_owner(monkeypatch):
    b = Backend(monkeypatch)
    r = run(system_name="Twin")
    assert r["error_code"] == "ambiguous_system" and r["missing"] == ["system_owner"]
    assert {c["owner"] for c in r["candidates"]} == {"bo", "alice"}
    ok = run(system_name="Twin", system_owner="alice")
    assert ok["system"]["id"] == "tw2"
    assert b.params("/causalgroup")["systemId"] == "tw2"


def test_wrong_owner_lists_real_owners(monkeypatch):
    Backend(monkeypatch)
    r = run(system_name="Shop", system_owner="alice")
    assert r["error_code"] == "system_not_found" and r["candidates"] == [{"name": "Shop", "owner": "bo"}]


def test_unknown_component_lists_components(monkeypatch):
    Backend(monkeypatch)
    r = run(system_name="Citizen Cane", component="Payments")
    assert r["error_code"] == "component_not_found"
    assert r["components"] == ["Checkout", "Jenkins", "Web Portal"]


def test_direction_or_hops_without_component_is_refused(monkeypatch):
    b = Backend(monkeypatch)
    assert run(system_name="Citizen Cane", direction="upstream")["error_code"] == "invalid_argument"
    assert run(system_name="Citizen Cane", hops=3)["missing"] == ["component"]
    assert not b.calls  # refused before any backend call


def test_out_of_range_arguments_are_refused(monkeypatch):
    # The HTTP endpoint skips schema validation, so the tool checks these itself.
    Backend(monkeypatch)
    assert run(system_name="Citizen Cane", component="Checkout", hops=9)["error_code"] == "invalid_argument"
    assert run(system_name="Citizen Cane", component="Checkout", hops="x")["error_code"] == "invalid_argument"
    assert run(system_name="Citizen Cane", direction="sideways")["error_code"] == "invalid_argument"


def test_backend_error_is_reported(monkeypatch):
    b = Backend(monkeypatch)
    b.handler = lambda request: (httpx.Response(200, json=SYSTEMS) if request.url.path.endswith("/systemframework")
                                 else httpx.Response(403))
    r = run(system_name="Citizen Cane")
    assert r["status"] == "error" and r["http_status"] == 403
