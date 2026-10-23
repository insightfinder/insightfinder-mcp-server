"""
Dependency graph tool for the InsightFinder MCP server (II-25333).

- get_dependency_graph: the component dependency graph of one system, as the Service Map's
  Dependency Graph tab shows it.

Data path, the same as the UI's: the system's causal group
(/api/external/v1/causalgroup?operation=candidates), then that group's edges
(/api/external/v1/dependencyrelation, hierarchical).

The tool states its call contract and enforces it: a missing, unknown or ambiguous system or
component is refused with a structured error (error_code, missing, next_step, ask_user and the
valid choices) rather than guessed. The HTTP tool endpoint calls tool functions directly, so
the parameter schema below is not validated at runtime; the checks here are the enforcement.
"""

import asyncio
import json
import logging
from collections import deque
from difflib import get_close_matches
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import Field

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client
from .system_info_tools import AI_MODEL, IT_SYSTEM, load_user_systems

logger = logging.getLogger(__name__)

_STATUS = {1: "confirmed", 2: "ignored", 3: "inferred", 4: "not_in_use"}
_SHOWN = ("confirmed", "inferred")  # what the UI draws
_DIRECTIONS = ("upstream", "downstream", "both")
_MAX_HOPS = 5
# mcp-service drops (does not truncate) a tool result over 100k chars of pretty-printed JSON;
# 150 edges stay well under it. Past this, the caller should focus on a component.
_MAX_EDGES = 150
_MAX_COMPONENTS = 300
# Looking a component name up across systems costs two backend calls a system.
_MAX_SCAN_SYSTEMS = 50
_SCAN_CONCURRENCY = 5


def _contract_error(code: str, message: str, next_step: str, ask_user: Optional[str] = None,
                    **extra: Any) -> Dict[str, Any]:
    """A refusal the calling agent can act on: what is wrong, what to do next, and what to ask
    the user when only the user can supply it."""
    error = {"status": "error", "error_code": code, "message": message, "next_step": next_step}
    if ask_user:
        error["ask_user"] = ask_user
    error.update(extra)
    return error


def _choices(systems: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [{"name": s["name"], "owner": s["owner"]} for s in systems]


def _resolve_system(systems: List[Dict[str, Any]], system_name: str,
                    system_owner: Optional[str]) -> Dict[str, Any]:
    """The one system named (display name or id, case-insensitive), or a contract error."""
    wanted = system_name.strip().casefold()
    matches = [s for s in systems if wanted in (s["name"].casefold(), s["id"].casefold())]
    if system_owner:
        owned = [s for s in matches if s["owner"].casefold() == system_owner.strip().casefold()]
        if matches and not owned:
            return _contract_error(
                "system_not_found",
                f"No system named '{system_name}' is owned by '{system_owner}'.",
                "Call again with one of the owners in candidates, or without system_owner.",
                candidates=_choices(matches))
        matches = owned
    if not matches:
        suggestions = get_close_matches(system_name.strip(), [s["name"] for s in systems],
                                        n=5, cutoff=0.5)
        return _contract_error(
            "system_not_found",
            f"'{system_name}' is not one of the user's systems.",
            "Use an exact name from available_systems. If the user named a component, service "
            "or project rather than a system, ask which system it belongs to. Do not guess "
            "from did_you_mean without the user's confirmation.",
            ask_user=f"I couldn't find a system called '{system_name}'. Which system do you mean?",
            missing=["system_name"], did_you_mean=suggestions,
            available_systems=_choices(systems))
    if len(matches) > 1:
        return _contract_error(
            "ambiguous_system",
            f"{len(matches)} systems are named '{system_name}'.",
            "Call again with system_owner set to the owner of the intended system.",
            ask_user=(f"There are several systems called '{system_name}' (owners: "
                      f"{', '.join(s['owner'] for s in matches)}). Which one do you mean?"),
            missing=["system_owner"], candidates=_choices(matches))
    return {"status": "success", "system": matches[0]}


def _pick_causal_group(data: Dict[str, Any], system: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The causal group the UI shows first: the system's configured analysis group, else the
    first non-correlation group by name."""
    candidates = [c for c in data.get("candidates") or [] if isinstance(c, dict) and c.get("causalKey")]
    configured = data.get("configuredCausalKey")
    if configured:
        hit = next((c for c in candidates if c["causalKey"] == configured), None)
        return {"key": configured, "name": (hit or {}).get("causalName"),
                "owner": (hit or {}).get("userName") or system["owner"], "configured": True}
    usable = sorted((c for c in candidates if not c.get("correlationGroup")),
                    key=lambda c: (c.get("causalName") or c["causalKey"]).casefold())
    if not usable:
        return None
    c = usable[0]
    return {"key": c["causalKey"], "name": c.get("causalName"),
            "owner": c.get("userName") or system["owner"], "configured": False}


def _edge(raw: Dict[str, Any], zone: str) -> Optional[Dict[str, Any]]:
    s, t = raw.get("s") or {}, raw.get("t") or {}
    if not s.get("id") or not t.get("id"):
        return None
    probability = raw.get("lap", raw.get("lmp"))
    return {
        "source": s["id"],
        "target": t["id"],
        "sourceType": "instance" if s.get("type") in ("i", "instanceLevel") else "component",
        "targetType": "instance" if t.get("type") in ("i", "instanceLevel") else "component",
        # The backend reports a relation without a status as in use (InsightFinder-inferred).
        "status": _STATUS.get(raw.get("st"), "inferred"),
        "probability": round(float(probability), 4) if probability is not None else None,
        "external": bool(raw.get("e", raw.get("ext", False))),
        "zones": [zone],
    }


def _instance_edge_rows(data: Any) -> List[Dict[str, Any]]:
    """instanceDependencyData arrives as {component: [edge]} or as a list (of edges, or of
    per-component groups); both shapes are accepted."""
    rows: List[Dict[str, Any]] = []
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for item in items:
        if isinstance(item, list):
            rows.extend(r for r in item if isinstance(r, dict))
        elif isinstance(item, dict) and "s" in item:
            rows.append(item)
        elif isinstance(item, dict):
            for value in item.values():
                if isinstance(value, list):
                    rows.extend(r for r in value if isinstance(r, dict))
    return rows


def _public(edge: Dict[str, Any]) -> Dict[str, Any]:
    """An edge as returned: endpoint types only when an end is an instance (mixed relations)."""
    if edge["sourceType"] == edge["targetType"] == "component":
        return {k: v for k, v in edge.items() if k not in ("sourceType", "targetType")}
    return edge


def _merge(edges: Dict[tuple, Dict[str, Any]], edge: Optional[Dict[str, Any]]) -> None:
    """Keep one edge per (source, target) across zones, with the highest probability."""
    if edge is None:
        return
    key = (edge["source"], edge["target"])
    seen = edges.get(key)
    if seen is None:
        edges[key] = edge
        return
    seen["zones"] = sorted(set(seen["zones"]) | set(edge["zones"]))
    if (edge["probability"] or 0) > (seen["probability"] or 0):
        seen.update({k: edge[k] for k in ("probability", "status", "external")})


def _components(meta: Dict[str, Any], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The components the UI lists: every component in a component-level relation, whatever its
    status (so one whose only edge is hidden still shows, unconnected), with its instance count
    from instanceMetaData. Components with no relation at all are not listed, as in the UI."""
    instances: Dict[str, set] = {}
    raw = (meta or {}).get("instanceMetaData")
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw or []
    except (json.JSONDecodeError, TypeError):
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("componentName"):
            continue
        names = instances.setdefault(entry["componentName"], set())
        try:
            members = json.loads(entry.get("instanceListStr") or "[]")
        except (json.JSONDecodeError, TypeError):
            members = []
        names.update(m.get("i") for m in members if isinstance(m, dict) and m.get("i"))
    names = {e[end] for e in edges for end in ("source", "target") if e[f"{end}Type"] == "component"}
    return [{"name": n, "instanceCount": len(instances.get(n, ()))}
            for n in sorted(names, key=str.casefold)]


def _focus(edges: List[Dict[str, Any]], component: str, direction: str, hops: int) -> List[Dict[str, Any]]:
    """Edges within `hops` of `component`: upstream walks to sources (what it depends on),
    downstream to targets (what depends on it)."""
    kept: Dict[tuple, Dict[str, Any]] = {}
    walks = ("upstream", "downstream") if direction == "both" else (direction,)
    for walk in walks:
        frontier, seen = deque([(component, 0)]), {component}
        while frontier:
            node, depth = frontier.popleft()
            if depth == hops:
                continue
            for e in edges:
                near, far = (e["target"], e["source"]) if walk == "upstream" else (e["source"], e["target"])
                if near == node:
                    kept[(e["source"], e["target"])] = e
                    if far not in seen:
                        seen.add(far)
                        frontier.append((far, depth + 1))
    return list(kept.values())


async def _load_graph(api_client, system: Dict[str, Any]) -> Dict[str, Any]:
    """The system's causal group and its merged edges: {"status": "success", "group" (None when
    the system has none), "zones", "component_edges", "instance_edges", "meta"} or an error."""
    cand = await api_client.get_causal_group_candidates(system["owner"], system["id"])
    if cand.get("status") == "error":
        return cand
    group = _pick_causal_group(cand.get("data") or {}, system)
    if group is None:
        return {"status": "success", "group": None}

    rel = await api_client.get_dependency_relation(group["key"], group["owner"])
    if rel.get("status") == "error":
        return rel
    data = rel.get("data") or {}
    dep_map = data.get("dependencyMap") or []
    entries = dep_map.values() if isinstance(dep_map, dict) else dep_map

    component_edges: Dict[tuple, Dict[str, Any]] = {}
    instance_edges: Dict[tuple, Dict[str, Any]] = {}
    zones = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        zone = (((entry.get("relationDependencyKey") or {}).get("relationDependencyPartitionKey")
                 or {}).get("zone")) or "NO_ZONE"
        zones.append(zone)
        for raw in entry.get("componentDependencyData") or entry.get("dependencyData") or []:
            if isinstance(raw, dict):
                _merge(component_edges, _edge(raw, zone))
        for raw in _instance_edge_rows(entry.get("instanceDependencyData")):
            _merge(instance_edges, _edge(raw, zone))
    return {"status": "success", "group": group, "zones": zones, "component_edges": component_edges,
            "instance_edges": instance_edges, "meta": data.get("metaData") or {}}


async def _systems_with_component(api_client, systems: List[Dict[str, Any]],
                                  name: str) -> List[Dict[str, Any]]:
    """The systems whose dependency graph lists a component called `name` (case-insensitive),
    as [{system, component}]. Systems whose graph cannot be read are skipped."""
    wanted = name.strip().casefold()
    gate = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def check(system):
        async with gate:
            graph = await _load_graph(api_client, system)
        if graph.get("status") != "success" or graph["group"] is None:
            return None
        edges = list(graph["component_edges"].values())
        for c in _components(graph["meta"], edges):
            if c["name"].casefold() == wanted:
                return {"system": system, "component": c["name"]}
        return None

    found = await asyncio.gather(*(check(s) for s in systems))
    return [f for f in found if f]


async def _component_not_system(api_client, systems: List[Dict[str, Any]], system_name: str,
                                not_found: Dict[str, Any]) -> Dict[str, Any]:
    """Callers often pass a component (service) name as system_name. When the name is a
    component of the user's systems, say which system holds it and how to call again."""
    if len(systems) > _MAX_SCAN_SYSTEMS:
        return {**not_found, "componentSearch": f"skipped: more than {_MAX_SCAN_SYSTEMS} systems"}
    hits = await _systems_with_component(api_client, systems, system_name)
    if not hits:
        return {**not_found, "componentSearch": "no system has a component with this name"}
    names = [h["system"]["name"] for h in hits]

    def retry(hit):
        s = hit["system"]
        args = {"system_name": s["name"], "component": hit["component"]}
        if sum(1 for x in systems if x["name"].casefold() == s["name"].casefold()) > 1:
            args["system_owner"] = s["owner"]
        return args

    if len(hits) == 1:
        h = hits[0]
        return _contract_error(
            "component_in_system",
            f"'{system_name}' is not a system; it is a component of system '{h['system']['name']}'.",
            "Call again with retry_with: it names that system and focuses on the component.",
            retry_with=retry(h))
    return _contract_error(
        "component_in_system",
        f"'{system_name}' is a component of {len(hits)} systems: {', '.join(names)}.",
        "Ask the user which system they mean, then call again with that candidate's retry_with.",
        ask_user=f"'{system_name}' appears in several systems ({', '.join(names)}). Which one do you mean?",
        missing=["system_name"],
        candidates=[{"system": h["system"]["name"], "owner": h["system"]["owner"],
                     "component": h["component"], "retry_with": retry(h)} for h in hits])


@mcp_server.tool()
async def get_dependency_graph(
    system_name: Annotated[str, Field(
        min_length=1,
        description="Exact name of ONE system of type \"IT System\", as listed by "
                    "get_system_list. Not an AI Model, component, service, project or "
                    "instance name. Required: if the request does not name a system, ask the "
                    "user which one instead of calling this tool.")],
    system_owner: Annotated[Optional[str], Field(
        description="Owner of the system. Required only when several systems share the name "
                    "(the tool then returns the candidates).")] = None,
    component: Annotated[Optional[str], Field(
        description="Exact component name in this system's graph to focus on, e.g. for 'what "
                    "does X depend on'. Omit for the whole graph.")] = None,
    direction: Annotated[Literal["upstream", "downstream", "both"], Field(
        description="With component only: upstream = what it depends on, downstream = what "
                    "depends on it.")] = "both",
    hops: Annotated[int, Field(
        ge=1, le=_MAX_HOPS,
        description="With component only: how many dependency steps to follow (1-5).")] = 1,
    include_inactive: Annotated[bool, Field(
        description="Also return edges marked ignored or not in use (hidden in the UI).")] = False,
    include_instances: Annotated[bool, Field(
        description="Also return instance-level edges.")] = False,
) -> Dict[str, Any]:
    """
    The dependency graph of one system: which components depend on which, as the Service Map's
    Dependency Graph tab shows it.

    **When to use this tool:**
    - "What does X depend on?", "What depends on X?", upstream/downstream, topology, service
      dependencies or the dependency graph of a system
    - Not for the root-cause chain of one incident: use get_incident_details with
      fetch_rca_chain=True

    **Call contract (enforced):** system_name is required and must be the exact name of one
    of the user's systems (see get_system_list). The tool refuses rather than guesses: an
    empty, unknown or ambiguous system, an unknown component, or direction/hops without a
    component returns status "error" with error_code, next_step, ask_user (a question to relay
    to the user) and the valid choices. A component name passed as system_name returns
    error_code "component_in_system" with retry_with, the exact arguments to call again with
    (or, when several systems have that component, candidates to ask the user about). An AI
    Model name (see get_system_list's type) returns error_code "not_an_it_system". Follow next_step; when only the user can resolve it, ask them ask_user
    and stop, do not pick a candidate yourself.

    An edge "source -> target" means source is upstream of target: target depends on source,
    and a problem in source can affect target.

    Returns:
        status "success" with system {name, id, owner}, causalGroup {key, name (may be null),
        owner, configured}, zones, components [{name, instanceCount}] (as listed in the UI,
        including ones whose only edge is hidden), edges [{source, target, status (confirmed:
        by CMDB or user | inferred: by InsightFinder), probability, external (from e.g.
        ServiceNow), zones}], counts, truncated, ui-url (the Dependency Graph tab), and focus
        when component is set; or a contract error as described above.
        A system without a causal group or without edges is a success with graphAvailable false
        and a message.
    """
    try:
        api_client = get_current_api_client()
        if not api_client:
            return {"status": "error",
                    "message": "No API client configured. Please configure your InsightFinder credentials."}

        # Arguments that need no backend call first.
        if direction not in _DIRECTIONS:
            return _contract_error("invalid_argument", f"direction must be one of {list(_DIRECTIONS)}.",
                                   "Call again with a valid direction.")
        try:
            hops = int(hops)
        except (TypeError, ValueError):
            hops = 0
        if not 1 <= hops <= _MAX_HOPS:
            return _contract_error("invalid_argument", f"hops must be an integer from 1 to {_MAX_HOPS}.",
                                   "Call again with a valid hops value.")
        component = (component or "").strip() or None
        if not component and (direction != "both" or hops != 1):
            return _contract_error(
                "invalid_argument", "direction and hops apply only to a focused component.",
                "Call again with component set, or without direction/hops for the whole graph.",
                missing=["component"])

        loaded = await load_user_systems(api_client)
        if loaded.get("status") == "error":
            return loaded
        # Dependency graphs exist for IT systems only; AI Models are AI WatchTower's.
        systems = [s for s in loaded["systems"] if s["type"] == IT_SYSTEM]
        if not (system_name or "").strip():
            return _contract_error(
                "missing_system", "system_name is required.",
                "Ask the user which system they mean; do not pick one yourself.",
                ask_user="Which system's dependency graph do you want?",
                missing=["system_name"], available_systems=_choices(systems))
        wanted = system_name.strip().casefold()
        models = [s for s in loaded["systems"] if s["type"] == AI_MODEL
                  and wanted in (s["name"].casefold(), s["id"].casefold())]
        if models and not any(wanted in (s["name"].casefold(), s["id"].casefold()) for s in systems):
            return _contract_error(
                "not_an_it_system",
                f"'{system_name}' is an AI Model monitored by AI WatchTower, not an IT System; "
                f"dependency graphs exist for IT Systems only.",
                "Do not retry this tool with it. Answer AI Model questions with the AI WatchTower "
                "tools, or ask the user which IT System they mean.",
                candidates=_choices(models), available_systems=_choices(systems))
        resolved = _resolve_system(systems, system_name, system_owner)
        if resolved["status"] != "success":
            if resolved["error_code"] == "system_not_found" and "did_you_mean" in resolved:
                return await _component_not_system(api_client, systems, system_name, resolved)
            return resolved
        system = resolved["system"]
        system_out = {"name": system["name"], "id": system["id"], "owner": system["owner"]}
        ui_url = (f"{api_client.base_url}/ui/global/ServiceMap?s={system['id']}"
                  f"&e=All&customerName={system['owner']}&tab=dependency")

        graph = await _load_graph(api_client, system)
        if graph.get("status") == "error":
            return graph
        group = graph["group"]
        if group is None:
            return {"status": "success", "graphAvailable": False, "system": system_out,
                    "message": f"System '{system['name']}' has no causal group, so it has no "
                               f"dependency graph yet.", "ui-url": ui_url}
        component_edges, instance_edges = graph["component_edges"], graph["instance_edges"]
        zones, data = graph["zones"], {"metaData": graph["meta"]}

        all_edges = sorted(component_edges.values(), key=lambda e: (e["source"].casefold(), e["target"].casefold()))
        components = _components(data.get("metaData") or {}, all_edges)
        hidden = {s: sum(1 for e in all_edges if e["status"] == s) for s in ("ignored", "not_in_use")}
        edges = all_edges if include_inactive else [e for e in all_edges if e["status"] in _SHOWN]

        focus = None
        if component:
            by_name = {c["name"].casefold(): c["name"] for c in components}
            name = by_name.get(component.casefold())
            if name is None:
                return _contract_error(
                    "component_not_found",
                    f"'{component}' is not a component of system '{system['name']}'.",
                    "Use an exact name from components, or ask the user which component they mean.",
                    ask_user=f"I couldn't find a component called '{component}' in {system['name']}. Which one do you mean?",
                    missing=["component"], components=[c["name"] for c in components],
                    system=system_out)
            edges = _focus(edges, name, direction, hops)
            focus = {"component": name, "direction": direction, "hops": hops}

        truncated = len(edges) > _MAX_EDGES or len(components) > _MAX_COMPONENTS
        result: Dict[str, Any] = {
            "status": "success",
            "graphAvailable": bool(all_edges),
            "system": system_out,
            "causalGroup": group,
            "zones": sorted(set(zones)),
            "edgeDirection": "source -> target: target depends on source (source is upstream)",
            "components": components[:_MAX_COMPONENTS],
            "edges": [_public(e) for e in edges[:_MAX_EDGES]],
            "counts": {"components": len(components), "edgesReturned": min(len(edges), _MAX_EDGES),
                       "edgesTotal": len(all_edges), "hiddenIgnored": 0 if include_inactive else hidden["ignored"],
                       "hiddenNotInUse": 0 if include_inactive else hidden["not_in_use"],
                       "instanceEdges": len(instance_edges)},
            "truncated": truncated,
            "ui-url": ui_url,
        }
        if truncated:
            result["message"] = (f"Showing {min(len(edges), _MAX_EDGES)} of {len(edges)} edges and "
                                 f"{min(len(components), _MAX_COMPONENTS)} of {len(components)} "
                                 f"components; set component to focus on part of the graph.")
        elif not all_edges:
            result["message"] = f"Causal group '{group['name']}' has no dependency edges yet."
        if focus:
            result["focus"] = focus
        if include_instances:
            inst = sorted(instance_edges.values(), key=lambda e: (e["source"].casefold(), e["target"].casefold()))
            if not include_inactive:
                inst = [e for e in inst if e["status"] in _SHOWN]
            result["instanceEdges"] = inst[:_MAX_EDGES]
        return result

    except Exception as e:
        logger.error(f"Error getting dependency graph: {str(e)}", exc_info=True)
        return {"status": "error", "message": f"Failed to get dependency graph: {str(e)}"}
