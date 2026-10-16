"""
AI WatchTower (AW) tools for the InsightFinder MCP server.

Tools:
- aw_list_models: AI/ML and LLM models monitored in AI WatchTower
- aw_list_monitors: monitors (drift, performance, bias, ...) and their types
- aw_get_data_quality_config: the rules of a DATA_QUALITY monitor (fields, groups, schema check, volume detection)
- aw_get_summary: unhealthy models with model/data drift, data quality and evaluation counts
"""

import difflib
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

from ..server import mcp_server
from ...api_client.aw_client import get_current_aw_client
from .aw_labels import SCHEMA_OPTIONS
from .get_time import parse_time_parameters

logger = logging.getLogger(__name__)

# Mirrors AW's MonitorType enum
MONITOR_TYPES = (
    "DATA_DRIFT", "DATA_NOISE", "DATA_QUALITY", "LLM_PERFORMANCE", "LLM_TRUST_AND_SAFETY",
    "MODEL_DRIFT", "MODEL_PERFORMANCE", "MODEL_BIAS", "MODEL_AVAILABILITY",
)


def _client():
    client = get_current_aw_client()
    if not client:
        return None, {"status": "error",
                      "message": "AW's address is unknown: AW_API_URL is not set on the MCP server and it cannot be "
                                 "derived from the InsightFinder URL in use (or no InsightFinder credentials were sent)."}
    return client, None


FETCH_PAGE_SIZE = 1000  # AW accepts this; its own order is random in organization mode, so we fetch all and page here
MAX_FETCH_PAGES = 10
MONITOR_CACHE_TTL = 60  # seconds; one chat turn calls several tools, which all need the same monitor list
_monitor_cache: Dict[Any, Any] = {}


def clear_monitor_cache() -> None:
    _monitor_cache.clear()


def _cache_key(client, monitor_name, monitor_type):
    """Who is asking (AW URL, user, license) and what: a cached list must never cross users."""
    license_key = str((getattr(client, "headers", None) or {}).get("X-Api-Key", ""))
    who = (getattr(client, "base_url", None), getattr(client, "user_name", None),
           hashlib.sha256(license_key.encode()).hexdigest()[:16]) if hasattr(client, "base_url") else id(client)
    return who, monitor_name, monitor_type


async def _fetch_all_monitors(client, monitor_name, monitor_type):
    """Every matching monitor, or the client's non-success result. AW's paging is unstable, so callers sort and page.
    A successful list is reused for MONITOR_CACHE_TTL seconds (ponytail: a new monitor shows up within a minute)."""
    key = _cache_key(client, monitor_name, monitor_type)
    hit = _monitor_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    result = await _fetch_all_monitors_uncached(client, monitor_name, monitor_type)
    if result["status"] == "success":
        _monitor_cache[key] = (time.monotonic() + MONITOR_CACHE_TTL, result)
    return result


async def _fetch_all_monitors_uncached(client, monitor_name, monitor_type):
    result = await client.list_monitors(monitor_name, monitor_type, 0, FETCH_PAGE_SIZE)
    if result["status"] != "success":
        return result
    data = result["data"]
    if not (isinstance(data, dict) and "content" in data):  # a single monitor comes back unpaged
        return {"status": "success", "items": [data], "complete": True}
    items, total_pages = list(data["content"]), data.get("totalPages", 1)
    for page in range(1, min(total_pages, MAX_FETCH_PAGES)):
        more = await client.list_monitors(monitor_name, monitor_type, page, FETCH_PAGE_SIZE)
        if more["status"] != "success":
            return more
        items += more["data"]["content"]
    unique = {(m.get("userName"), m.get("monitorName")): m for m in items}  # drop repeats from unstable paging
    return {"status": "success", "items": list(unique.values()), "complete": total_pages <= MAX_FETCH_PAGES}


def _pick(item: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    return {k: item[k] for k in keys if item.get(k) is not None}


@mcp_server.tool()
async def aw_list_models(model_name: Optional[str] = None,
                         model_version: Optional[str] = None,
                         limit: int = 25) -> Dict[str, Any]:
    """
    List the AI/ML and LLM models monitored in AI WatchTower (AW).

    Use this tool when the user asks which models they have in AI WatchTower, what versions or
    environments exist, or how many anomalies a model has. For which models are unhealthy or
    drifting, use aw_get_summary instead.

    Args:
        model_name (str, optional): Only models whose name contains this text (case-insensitive).
        model_version (str, optional): Only models whose version contains this text (case-insensitive).
        limit (int): Maximum models to return, 1-100 (default 25). `total` is the full match count and
            `truncated` is true when more matched than were returned; narrow the filters to see the rest.

    Returns:
        {"status": "success", "total": int, "truncated": bool, "models": [{modelName, modelType,
        modelVersion, environment, anomalyCount, description}]}
    """
    try:
        if not 1 <= limit <= 100:
            return {"status": "error", "message": "limit must be between 1 and 100"}
        client, err = _client()
        if err:
            return err
        result = await client.list_models()
        if result["status"] != "success":
            return result
        data = result["data"]
        items = data if isinstance(data, list) else [data]
        if model_name:
            items = [m for m in items if model_name.lower() in str(m.get("modelName", "")).lower()]
        if model_version:
            items = [m for m in items if model_version.lower() in str(m.get("modelVersion", "")).lower()]
        models = [_pick(m, ["modelName", "modelType", "modelVersion", "environment", "anomalyCount",
                            "description"]) for m in items[:limit]]
        return {"status": "success", "total": len(items), "truncated": len(items) > limit, "models": models}
    except Exception as e:
        logger.error(f"Error in aw_list_models: {e}")
        return {"status": "error", "message": f"Error in aw_list_models: {e}"}


@mcp_server.tool()
async def aw_list_monitors(monitor_name: Optional[str] = None,
                           monitor_type: Optional[str] = None,
                           page: int = 0,
                           page_size: int = 10) -> Dict[str, Any]:
    """
    List the monitors configured in AI WatchTower (AW), such as drift, performance, bias and
    availability monitors.

    Use this tool when the user asks which monitors exist, which models have a data drift or
    model drift monitor, or how a model is being watched. It lists monitor configuration only,
    not drift results; for results use aw_get_summary.

    Results can include monitors owned by other users in the organization (see userName, the owner),
    depending on the user's "My Monitors" setting in AI WatchTower. Monitors are sorted by name, so
    paging is stable.

    Args:
        monitor_name (str, optional): Return only this monitor.
        monitor_type (str, optional): One of DATA_DRIFT, DATA_NOISE, DATA_QUALITY, LLM_PERFORMANCE,
            LLM_TRUST_AND_SAFETY, MODEL_DRIFT, MODEL_PERFORMANCE, MODEL_BIAS, MODEL_AVAILABILITY.
        page (int): Page number, starting at 0.
        page_size (int): Monitors per page, 1-100 (default 10).

    Returns:
        {"status": "success", "monitors": [{monitorName, userName (owner), monitorType, modelName,
        version, environment, monitorDesc}], "pagination": {currentPage, pageSize, totalPages,
        totalCount}}
    """
    try:
        if monitor_type:
            monitor_type = monitor_type.upper()
            if monitor_type not in MONITOR_TYPES:
                return {"status": "error",
                        "message": f"Invalid monitor_type. Use one of: {', '.join(MONITOR_TYPES)}"}
        if page < 0 or not 1 <= page_size <= 100:
            return {"status": "error", "message": "page must be >= 0 and page_size between 1 and 100"}
        client, err = _client()
        if err:
            return err
        result = await _fetch_all_monitors(client, monitor_name, monitor_type)
        if result["status"] != "success":
            return result
        items = sorted(result["items"], key=lambda m: (str(m.get("monitorName", "")).lower(),
                                                       str(m.get("userName", "")).lower()))
        start = page * page_size
        monitors = [_pick(m, ["monitorName", "userName", "monitorType", "modelName", "version",
                              "environment", "monitorDesc"]) for m in items[start:start + page_size]]
        pagination = {"currentPage": page, "pageSize": page_size,
                      "totalPages": max(1, -(-len(items) // page_size)), "totalCount": len(items)}
        if not result["complete"]:
            pagination["truncated"] = True
        return {"status": "success", "monitors": monitors, "pagination": pagination}
    except Exception as e:
        logger.error(f"Error in aw_list_monitors: {e}")
        return {"status": "error", "message": f"Error in aw_list_monitors: {e}"}


@mcp_server.tool()
async def aw_get_summary(start_time: Optional[str] = None,
                         end_time: Optional[str] = None) -> Dict[str, Any]:
    """
    Summarize the health of the user's AI/ML and LLM models in AI WatchTower (AW) for a time window.

    Use this tool when the user asks which models are unhealthy, how many models have model
    drift or data drift, or about data quality anomalies, performance anomalies and failed
    evaluations. It returns counts and the top 5 unhealthy models, not drift charts.

    How to read the numbers: only models that have data in the window are counted (totalModels is
    not the number of registered models; use aw_list_models for that). A model is unhealthy when it
    has any issue count above zero. Failed evaluations count violations, not distinct prompts. Data
    quality anomalies cover integrity and consistency issues only; volume and schema issues are not
    included. The summary always covers the whole organization, regardless of "My Monitors".

    Args:
        start_time (str, optional): Start of the window: "2026-02-12", "yesterday", "thisweek",
            or milliseconds. Defaults to today (UTC).
        end_time (str, optional): End of the window, same formats. Defaults to the end of today.

    Returns:
        {"status": "success", "summary": {totalModels, totalUnhealthyModels, totalModelDrift,
        totalDataDrift, ...}, "topUnhealthyModels": [{rank, modelName, modelVersion, totalIssues,
        modelDrift, dataDrift, ...}]}
    """
    try:
        start_ms = end_ms = 0  # 0 -> AW uses today (UTC)
        if start_time or end_time:
            try:
                start_ms, end_ms = parse_time_parameters(start_time, end_time, "UTC")
            except ValueError as e:
                return {"status": "error", "message": f"Invalid time range: {e}"}
            start_ms, end_ms = start_ms or 0, end_ms or 0
        client, err = _client()
        if err:
            return err
        result = await client.get_summary(start_ms, end_ms)
        if result["status"] != "success":
            return result
        data = result["data"]
        summary = _pick(data, ["totalModels", "totalUnhealthyModels", "totalPerformanceAnomalies",
                               "totalFailedEvaluations", "totalModelDrift", "totalDataDrift",
                               "totalDataQualityAnomalies", "totalDataNoiseRecords", "avgNoiseRatio",
                               "hasModelSetup"])
        top = [_pick(m, ["rank", "modelName", "modelVersion", "modelType", "totalIssues",
                         "performanceAnomalies", "failedEvaluations", "modelDrift", "dataDrift",
                         "dataQualityAnomalies", "dataNoiseRecords"])
               for m in data.get("topUnhealthyModels") or []]
        return {"status": "success", "summary": summary, "topUnhealthyModels": top}
    except Exception as e:
        logger.error(f"Error in aw_get_summary: {e}")
        return {"status": "error", "message": f"Error in aw_get_summary: {e}"}


MAX_DQ_FIELDS = 30


def _dq_digest(m: Dict[str, Any]) -> Dict[str, Any]:
    """Small digest of a DATA_QUALITY monitor; keys are exactly AW's (monitorSetting.keys, dataQualityTypes, ...)."""
    s = m.get("monitorSetting") or {}
    keys = s.get("keys") or {}
    # fields with rules beyond plain MISSING_DATA first, so a 340-field monitor still shows its interesting rules
    ordered = sorted(keys.items(), key=lambda kv: -len([t for t in kv[1].get("dataQualityTypes") or []
                                                         if t != "MISSING_DATA"]))
    fields = []
    for name, f in ordered[:MAX_DQ_FIELDS]:
        row = {"field": name, "fieldType": f.get("fieldType"), "dataQualityTypes": f.get("dataQualityTypes") or []}
        row.update(_pick(f, ["customRegex", "threshold", "order"]))
        if f.get("consistencyKeys"):
            row["consistencyKeys"] = f["consistencyKeys"]
        fields.append(row)
    summary: Dict[str, int] = {}
    for f in keys.values():
        for t in f.get("dataQualityTypes") or []:
            summary[t] = summary.get(t, 0) + 1
    schema_on = bool(s.get("schemaCheckEnabled"))
    schema = {"enabled": schema_on,
              "kinds": [SCHEMA_OPTIONS.get(k, k) for k, v in (s.get("schemaCheck") or {}).items() if v]
              if schema_on else []}
    volume = {"enabled": bool(s.get("volumeDetectionEnabled"))}
    if volume["enabled"]:
        volume.update(_pick(s, ["volumeDetectionMode", "volumeSensitivity", "volumeThresholdsEnabled",
                                "volumeThresholds"]))
        volume["mode"] = volume.pop("volumeDetectionMode", None)
    # groupKeys is {group: {field: full rule setting}}; keep only group -> field names
    groups = {"groupNameKey": s.get("groupNameKey"),
              "groupKeys": {g: list(f)[:MAX_DQ_FIELDS] for g, f in (s.get("groupKeys") or {}).items()}}
    sources = [_pick(d, ["name", "dataType", "dataSource"]) for d in m.get("dataSources") or []]
    cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    lines = [f"**Data quality rules: {m.get('monitorName')}** (owner: {m.get('userName')}, model: "
             f"{m.get('modelName')} {m.get('version')}, environment: {m.get('environment')})", "",
             "| Field | Type | Rules | Pattern / threshold |", "|---|---|---|---|"]
    for f in fields:
        pat = " ".join(str(f[k]) for k in ("customRegex", "threshold") if f.get(k) is not None)
        lines.append(f"| {cell(f['field'])} | {f['fieldType']} | {cell(', '.join(f['dataQualityTypes']))} | {cell(pat)} |")
    if len(keys) > len(fields):
        lines.append(f"\nShowing {len(fields)} of {len(keys)} fields.")
    lines.append(f"\nSchema Check: {'Enabled (' + ', '.join(schema['kinds']) + ')' if schema_on and schema['kinds'] else 'Enabled' if schema_on else 'Disabled'}. "
                 f"Volume detection: {'on, mode ' + str(volume.get('mode') or 'not set') + ', sensitivity ' + str(volume.get('volumeSensitivity')) if volume['enabled'] else 'off'}.")
    return {"monitorName": m.get("monitorName"), "owner": m.get("userName"), "model": m.get("modelName"),
            "version": m.get("version"), "environment": m.get("environment"), "dataSources": sources,
            "total_fields": len(keys), "truncated": len(keys) > len(fields), "fields": fields,
            "has_field_rules": any(f.get("dataQualityTypes") for f in keys.values()),
            "rule_summary": summary, "groups": groups, "schemaCheck": schema, "volumeDetection": volume,
            "verbatim_markdown": "\n".join(lines)}


# What a user may call a monitor, in the order the lookup tries it.
_NAME_FIELDS = (("monitor name", lambda m: m.get("monitorName")), ("model", lambda m: m.get("modelName")),
                ("owner", lambda m: m.get("userName")),
                ("data source", lambda m: [d.get("name") or d.get("dataSource") for d in m.get("dataSources") or []]),
                ("environment", lambda m: m.get("environment")))


def _norm(value) -> List[str]:
    return [" ".join(str(v).lower().split()) for v in (value if isinstance(value, list) else [value]) if v]


def find_monitors(items: List[Dict[str, Any]], query: Optional[str]):
    """(how it matched, monitors): the query, in any letter case, as a whole monitor name, model, owner, data source or
    environment, then as a part of any of them (3+ characters). (None, []) when nothing fits."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return None, []
    for label, get in _NAME_FIELDS:
        hits = [m for m in items if q in _norm(get(m))]
        if hits:
            return label, hits
    if len(q) >= 3:
        hits = [m for m in items if any(q in v for _, get in _NAME_FIELDS for v in _norm(get(m)))]
        if hits:
            return "part of a name", hits
    return None, []


def closest_names(items: List[Dict[str, Any]], query: str, cutoff: float = 0.5) -> List[str]:
    names = sorted({m.get("monitorName") for m in items if m.get("monitorName")})
    return difflib.get_close_matches(query, names, n=5, cutoff=cutoff) or \
        [n for n in names if query.lower() in n.lower()][:5]


def _monitor_label(m: Dict[str, Any]) -> str:
    return f'monitor "{m.get("monitorName")}" (owner: {m.get("userName")})'


async def _resolve_dq_monitor(client, monitor_name: str, owner: Optional[str]):
    """(monitor, None) or (None, tool result) for a DATA_QUALITY monitor: owner auto-resolved, ambiguous/not_found."""
    result = await _fetch_all_monitors(client, None, "DATA_QUALITY")  # a name filter hides other owners' monitors
    if result["status"] == "empty":
        result = {"status": "success", "items": []}
    if result["status"] != "success":
        return None, result
    found = [m for m in result["items"] if m.get("monitorName") == monitor_name
             and (owner is None or m.get("userName") == owner)]
    if len(found) > 1:
        return None, {"status": "ambiguous", "message": "Several owners have this monitor; ask which one.",
                      "candidates": [_pick(m, ["monitorName", "userName", "modelName", "environment"])
                                     for m in found]}
    if not found and owner:  # not in the caller's list: ask AW directly (403 if the caller may not see it)
        one = await client.get_monitor(owner, monitor_name)
        if one["status"] == "success" and isinstance(one["data"], dict) \
                and one["data"].get("monitorType") == "DATA_QUALITY":
            found = [one["data"]]
    if not found:
        pool = [m for m in result["items"] if owner is None or m.get("userName") == owner]
        label, hits = find_monitors(pool, monitor_name)
        if not hits:  # a typo: one clearly closest monitor name is used, several are offered
            near = closest_names(pool, monitor_name, 0.85)
            if len(near) == 1:
                label, hits = "closest name", [m for m in pool if m.get("monitorName") == near[0]]
        if label == "monitor name" and len({(m.get("userName")) for m in hits}) > 1:
            return None, {"status": "ambiguous", "message": "Several owners have this monitor; ask which one.",
                          "candidates": [_pick(m, ["monitorName", "userName", "modelName", "environment"]) for m in hits]}
        if len(hits) == 1:  # one monitor, however it was named: use it and say how it was read
            return {**hits[0], "_interpreted": f'"{monitor_name}" was read as {label}: {_monitor_label(hits[0])}. '}, None
        if hits:  # several monitors share the model, owner, data source or part of a name the user gave
            names = sorted({m.get("monitorName") for m in hits})
            model = hits[0].get("modelName") if label == "model" else None
            lead = (f'No monitor is named "{monitor_name}"; it is a model, so here are its monitors. ' if label == "model" else
                    f'No monitor is named "{monitor_name}"; it matches the {label}, so here are its {len(names)} monitors. ')
            return None, {"status": "model_match" if label == "model" else "multi_match", "matchedBy": label,
                          "modelName": model, "monitors": names, "lead": lead,
                          "candidates": [_pick(m, ["monitorName", "userName"]) for m in hits],
                          "message": f"'{monitor_name}' matches the {label}, not one monitor. Its monitors: "
                                     f"{', '.join(names)}; ask which monitor the user means."}
        close = closest_names(pool, monitor_name)
        return None, {"status": "not_found", "message": f"No DATA_QUALITY monitor named '{monitor_name}'"
                      + (f" for owner '{owner}'" if owner else "") + " is visible to this user."
                      + (" Closest names: " + ", ".join(close) + "; ask the user which one they mean." if close
                         else " Offer to list the monitors."),
                      "closestNames": close}
    return found[0], None


@mcp_server.tool()
async def aw_get_data_quality_config(monitor_name: str, owner: Optional[str] = None) -> Dict[str, Any]:
    """
    Read the configuration (rules) of a DATA_QUALITY monitor in AI WatchTower (AW).

    Use this tool when the user asks which data quality rules, checks or fields a monitor has, how
    volume detection or the schema check is set up, or which groups and data sources it covers. It
    returns configuration only, not issues found; it is a digest (at most 30 fields, with total_fields
    and truncated) so large monitors stay readable. Rule types are AW's own names (MISSING_DATA, CUSTOM,
    FIELD_TYPE_MISMATCH, DATA_OUTLIERS, CONSISTENCY, QUALIFIED_DATA_RANGE; the Workbench shows them as Missing Data,
    Field Type Mismatch, Data Outliers, Inconsistency, Disqualified Data Range, Custom). Use it for configuration questions
    only, never for counts. Append `verbatim_markdown`
    to the answer as is.

    Args:
        monitor_name (str): The monitor name; a model name, owner name, data source or part of a name also works (one
            match is used, several are listed as {"status": "model_match" | "multi_match"}).
        owner (str, optional): Monitor owner (userName). If omitted it is looked up; when several
            owners have a monitor of that name the result is {"status": "ambiguous", "candidates": [...]}
            and the user must pick one.

    Returns:
        {"status": "success", "scope": {"owner"}, monitorName, owner, model, version, environment,
        dataSources, total_fields, truncated, fields, has_field_rules, rule_summary, groups,
        schemaCheck, volumeDetection, verbatim_markdown} | {"status": "not_found" | "ambiguous" | "error"}
    """
    try:
        if not monitor_name:
            return {"status": "error", "message": "monitor_name is required"}
        client, err = _client()
        if err:
            return err
        monitor, failure = await _resolve_dq_monitor(client, monitor_name, owner)
        if failure:
            return failure
        digest = _dq_digest(monitor)
        note = {"interpreted": monitor["_interpreted"].strip()} if monitor.get("_interpreted") else {}
        return {"status": "success", "scope": {"owner": digest["owner"]}, **note, **digest}
    except Exception as e:
        logger.error(f"Error in aw_get_data_quality_config: {e}")
        return {"status": "error", "message": f"Error in aw_get_data_quality_config: {e}"}
