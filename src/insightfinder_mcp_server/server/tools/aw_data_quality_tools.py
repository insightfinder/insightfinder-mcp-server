"""
AI WatchTower (AW) data quality result tools (AW-2390). Coded against the AW external API contract:
AW-2390-data-quality-api-contract.md. Requests carry REAL UTC epoch ms; day keys in responses are the owner's
local midnight written as UTC ("fake UTC") and are only formatted as plain UTC dates, never shifted.

Tools:
- aw_get_data_quality: flagged entries of one DATA_QUALITY monitor (digest + examples)
- aw_get_data_quality_volume: per-day record volume vs baseline for one monitor
- aw_get_data_quality_overview: which data quality monitors have the most flagged entries
"""

import asyncio
import calendar
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..server import mcp_server
from .aw_tools import _client, _pick, _resolve_dq_monitor
from .get_time import parse_time_parameters

logger = logging.getLogger(__name__)

DAY_MS = 86_400_000
MAX_DAYS = 31
MAX_LIST = 30
MAX_EXAMPLES = 50  # events per page
MAX_ROWS_PER_EVENT = 10
MAX_ROWS = 100
OVERVIEW_CONCURRENCY = 5

# Contract section 1.2: subTypeMap ids. integrity = 2..9, consistency = 1, schema = 7..9 (as agreed in the plan).
RULE_IDS = {"CONSISTENCY": 1, "MISSING_DATA": 2, "DATA_OUTLIERS": 3, "FIELD_TYPE_MISMATCH": 4,
            "QUALIFIED_DATA_RANGE": 5, "CUSTOM": 6}
ISSUE_TYPES = {"integrity": list(range(2, 10)), "consistency": [1], "schema": [7, 8, 9],
               **{k: [v] for k, v in RULE_IDS.items()}}


def _cell(v, limit: Optional[int] = None) -> str:
    """Customer text made safe for a markdown cell, caption or JSON value: no table breaks, images, links or HTML."""
    s = str(v)
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    s = s.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", " ")
    return s.replace("![", "! [").replace("](", "] (").replace("<", "&lt;").replace(">", "&gt;")


def _day(ms) -> str:
    """Owner-local calendar date of a day key: plain UTC formatting, no timezone shift (contract 3.3)."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _iso(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _range(start_time: Optional[str], end_time: Optional[str]):
    """(start_ms, end_ms) as REAL UTC epoch ms. UTC parse on purpose: parse_time_parameters with 'UTC' yields real
    UTC, and AW does the owner-timezone shift itself. Default window: the last 7 days."""
    now = int(time.time() * 1000)
    if not start_time and not end_time:
        return now - 7 * DAY_MS, now
    # "3d", "last 3 days", "24h": the last N days or hours before end_time (default now); a model does not know today's date
    span = 7 * DAY_MS
    rel = re.fullmatch(r"(?:last[_ ]?)?(\d{1,3})[_ ]?(h|hrs?|hours?|d|days?)", str(start_time or "").strip().lower())
    if rel:
        if int(rel.group(1)) < 1:
            raise ValueError("the relative amount must be at least 1 (for example '1d' or '24h')")
        span = int(rel.group(1)) * (3600000 if rel.group(2).startswith("h") else DAY_MS)
        start_time = None
    s, e = parse_time_parameters(start_time, end_time, "UTC")
    if e is not None and isinstance(end_time, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_time) and e % DAY_MS == 0:
        e += DAY_MS - 1  # a plain end date means the whole day
    e = now if e is None else e
    s = e - span if s is None else s
    if s > e:
        raise ValueError("start_time is after end_time")
    if e - s > 366 * DAY_MS:
        raise ValueError("the time range cannot be longer than 366 days")
    return int(s), int(e)


def _failure(result: Dict[str, Any]) -> Dict[str, Any]:
    """Plain-English tool result for a non-success AW client result (HTTP 401/403/404/400/502 mapped)."""
    code, detail = result.get("http_status"), result.get("detail")
    if result.get("status") == "empty":
        return {"status": "error", "message": "AW returned an empty response; try again or check the monitor."}
    msgs = {401: "AW rejected the credentials (HTTP 401). Check the InsightFinder user name and license key.",
            403: "This user is not allowed to view that monitor's data (HTTP 403).",
            400: "AW rejected the request as invalid (HTTP 400)" + (f": {detail}" if detail else "."),
            502: "AW could not get data quality results from the InsightFinder backend (HTTP 502)"
                 + (f": {detail}" if detail else ".")}
    if code == 404:
        return {"status": "not_found", "message": "AW says the monitor was not found (HTTP 404)"
                + (f": {detail}" if detail else ".")}
    return {"status": "error", "message": msgs.get(code, result.get("message", "AW request failed."))}


def _day_key(day: Optional[str]) -> Optional[int]:
    if day is None or day == "":
        return None
    d = str(day).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):  # owner-local midnight written as UTC, same as AW's keys
        return calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
    if re.fullmatch(r"\d{10,13}", d):  # a day key copied from an earlier result, sent back verbatim
        return int(d)
    raise ValueError("day must be YYYY-MM-DD or a day key (epoch ms) from an earlier result")


def _days(dist: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for key, v in dist.items():
        d = (v or {}).get("distribution") or {}
        rows.append({"day": _day(key), "dayKey": int(key), "recordsChecked": (v or {}).get("totalCount", 0),
                     "flaggedEntries": sum(d.values()), "byRuleType": d})
    return sorted(rows, key=lambda r: r["dayKey"])


def _example(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Trimmed records: one per info item (a flagged field), keys per contract 1.2."""
    side = ev.get("target") or ev.get("source") or {}
    out = []
    for i in side.get("info") or [{}]:
        out.append({"time": _iso(ev["timestamp"]) if ev.get("timestamp") else None,
                    "rule": _cell(i.get("subTypeStr") or i.get("anomalyType") or ev.get("dataQualityType")),
                    "field": _cell(i["key"]) if i.get("key") is not None else None,
                    "value": _cell(i["value"], 80) if i.get("value") is not None else None,
                    "correctValue": _cell(i["correctValue"], 80) if i.get("correctValue") is not None else None,
                    "group": _cell(i["groupName"]) if i.get("groupName") is not None else None})
    return out


def _scope_line(owner, tz, s, e, source) -> str:
    return (f"owner: {_cell(owner)}, owner timezone: {tz or 'unknown'}, window (UTC): {_iso(s)} to {_iso(e)}, "
            f"data source: {source}")


DEFAULT_SOURCE = "default (AW uses the first data source that has data)"
FILTER_NOTE = "The per-rule counts and the per-day table cover the whole window and are NOT filtered."


def _source(m: Dict[str, Any], data_source: Optional[str]):
    """(name to send to AW, error result): the monitor's own data source, matched case-insensitively."""
    if not data_source:
        return None, None
    names = [d.get("name") or d.get("dataSource") for d in m.get("dataSources") or []]
    for n in names:
        if n and n.lower() == data_source.strip().lower():
            return n, None
    if not names:
        return data_source, None
    return None, {"status": "error", "message": f"Unknown data source '{_cell(data_source)}' for monitor "
                  f"'{_cell(m.get('monitorName'))}'; valid data sources: {', '.join(_cell(n) for n in names if n)}"}


def _resolve_failure(failure: Dict[str, Any]) -> Dict[str, Any]:
    return _failure(failure) if failure.get("http_status") else failure


def _dq_result(data: Dict[str, Any], m: Dict[str, Any], s: int, e: int, filters: Dict[str, Any],
               page: int, page_size: int, n_examples: int, source: str = DEFAULT_SOURCE) -> Dict[str, Any]:
    days = _days(data.get("subTypeDistribution") or {})
    checked = data.get("recordsChecked")
    checked = sum(d["recordsChecked"] for d in days) if checked is None else checked
    total = data.get("totalAnomalyCount") or 0
    by_rule: Dict[str, int] = {}
    for d in days:
        for k, v in d["byRuleType"].items():
            by_rule[k] = by_rule.get(k, 0) + v
    events = data.get("events") or []
    examples, top, more = [], {}, 0
    for ev in events:
        rows = _example(ev)
        more += max(0, len(rows) - MAX_ROWS_PER_EVENT)
        for ex in rows[:MAX_ROWS_PER_EVENT]:
            if len(examples) < n_examples:
                examples.append(ex)
            else:
                more += 1
            if ex["field"]:
                top[ex["field"]] = top.get(ex["field"], 0) + 1
    top_fields = [{"field": f, "flaggedEntriesOnPage": c} for f, c in sorted(top.items(), key=lambda kv: -kv[1])][:MAX_LIST]
    fields, groups = sorted(_cell(f) for f in data.get("fields") or []), sorted(_cell(g) for g in data.get("groups") or [])
    shown_days = days[-MAX_DAYS:]
    tz = data.get("ownerTimezone")
    # AW filters only totalAnomalyCount and events; per-rule counts, the per-day rows and recordsChecked stay whole-window
    active = {k: v for k, v in filters.items() if k in ("issue_type", "fields", "group", "day")}
    desc = ", ".join(f"{k}={_cell(','.join(v) if isinstance(v, list) else v)}" for k, v in active.items())
    if checked == 0:
        status, msg = "no_data", "The monitor exists but nothing was checked in this window (no records arrived, or no rules are active)."
    elif total == 0:
        status = "no_flagged_entries"
        msg = (f"No flagged entries match this filter ({checked} records were checked in the window)." if active
               else f"{checked} records were checked and no flagged entries were found.")
    else:
        status = "success"
        msg = f"{total} flagged entries match your filter ({desc}). {FILTER_NOTE}" if active else \
            f"{total} flagged entries in {checked} records checked."
    name = _cell(m.get("monitorName"))
    lines = [f"**Data quality: {name}** ({_scope_line(m.get('userName'), tz, s, e, source)})", "", msg, ""]
    if active:
        lines += ([] if FILTER_NOTE in msg else [FILTER_NOTE, ""]) + ["Per-day counts for the whole window (not filtered):", ""]
    lines += ["| Day | Records checked | Flagged entries | By rule type |", "|---|---|---|---|"]
    lines += [f"| {r['day']} | {r['recordsChecked']} | {r['flaggedEntries']} | "
              f"{_cell(', '.join(f'{k}: {v}' for k, v in r['byRuleType'].items()) or '-')} |" for r in shown_days]
    if len(days) > MAX_DAYS:
        lines.append(f"\nShowing the latest {MAX_DAYS} of {len(days)} days.")
    if examples:
        lines += ["", f"Example flagged entries (page {page} of events, {page_size} events per page):", "",
                  "| Time | Rule | Field | Value | Correct value | Group |", "|---|---|---|---|---|---|"]
        lines += [f"| {x['time']} | {x['rule']} | {x['field']} | {x['value']} | {x['correctValue']} | {x['group']} |"
                  for x in examples]
    pages = max(1, -(-total // page_size))
    res = {"status": status, "message": msg, "monitorFound": True, "monitorName": name,
            "owner": _cell(m.get("userName")), "scope": {"owner": _cell(m.get("userName"))}, "ownerTimezone": tz,
            "dataSource": source,
            "timeRange": {"startMs": s, "endMs": e, "start": _iso(s), "end": _iso(e), "timezone": "UTC"},
            "filters": filters, "recordsChecked": checked, "totalFlaggedEntries": total,
            "countsByRuleType": by_rule, "daily": shown_days, "daily_truncated": len(days) > MAX_DAYS,
            "topFields": top_fields, "topFieldsBasis": "records on this page only",
            "fieldsAvailable": fields[:MAX_LIST], "total_fields_available": len(fields),
            "groupsAvailable": groups[:MAX_LIST], "total_groups_available": len(groups),
            "examples": examples,
            "paging": {"page": page, "pageSize": page_size, "unit": "events", "totalFlaggedEntries": total,
                       "totalPages": pages, "hasMore": len(events) == page_size and page * page_size < total},
            "truncated": len(days) > MAX_DAYS or len(fields) > MAX_LIST or len(groups) > MAX_LIST or more > 0,
            "verbatim_markdown": "\n".join(lines)}
    if more:
        res["examplesNote"] = f"and {more} more issue rows not shown (at most {MAX_ROWS_PER_EVENT} per event, {MAX_ROWS} in total)"
    if active:
        res.update(filtersActive=True, note=FILTER_NOTE)
    return res


@mcp_server.tool()
async def aw_get_data_quality(monitor_name: str,
                              owner: Optional[str] = None,
                              start_time: Optional[str] = None,
                              end_time: Optional[str] = None,
                              data_source: Optional[str] = None,
                              fields: Optional[List[str]] = None,
                              issue_type: Optional[str] = None,
                              group: Optional[str] = None,
                              day: Optional[str] = None,
                              page: int = 1,
                              page_size: int = 10) -> Dict[str, Any]:
    """
    Get the data quality problems (flagged entries) found by a DATA_QUALITY monitor in AI WatchTower (AW).

    Use this tool when the user asks what data quality issues a monitor has, which rules or fields fail, how
    many flagged entries there are per day, or wants example bad records. For the monitor's rules use
    aw_get_data_quality_config; for record volume use aw_get_data_quality_volume; to compare monitors use
    aw_get_data_quality_overview. Counts are flagged entries (one record can carry several), not distinct bad
    rows. Append `verbatim_markdown` to the answer as is.

    Status: success; no_flagged_entries (records were checked, none flagged); no_data (monitor found but nothing
    was checked in the window); not_found; ambiguous (several owners, ask which); error.

    Args:
        monitor_name (str): Exact monitor name.
        owner (str, optional): Monitor owner (userName); looked up when omitted.
        start_time (str, optional): "2026-10-05", "3d" (the last 3 days), "24h", "yesterday", "lastweek" or epoch ms. Default: last 7 days.
            "3d"/"24h" count back from end_time when it is given (at least 1).
        end_time (str, optional): Same formats; a plain date includes that whole day. Times are UTC.
        data_source (str, optional): One data source name of the monitor (case-insensitive); an unknown name is
            an error listing the valid ones. Default: AW uses the first data source that has data.
        fields (list[str], optional): Only these fields.
        issue_type (str, optional): integrity, consistency, schema, or one rule: MISSING_DATA, DATA_OUTLIERS,
            FIELD_TYPE_MISMATCH, QUALIFIED_DATA_RANGE, CUSTOM, CONSISTENCY.
        group (str, optional): Only this group value.
        day (str, optional): Drill into one day: "YYYY-MM-DD" (owner-local date) or a day key from an earlier result.
        page (int): Page of events, from 1. AW pages by event and one event can carry several flagged fields.
        page_size (int): Events per page, 1-50 (default 10). Every issue row of an event is shown, at most 10 per
            event and 100 in total (`examplesNote` says how many were left out).

    When issue_type, fields, group or day is set, only totalFlaggedEntries and the examples are filtered;
    countsByRuleType, daily and recordsChecked cover the whole window (`filtersActive`, `note`).

    Returns:
        {"status", "message", "owner", "ownerTimezone", "timeRange", "recordsChecked", "totalFlaggedEntries",
        "countsByRuleType", "daily": [{day, dayKey, recordsChecked, flaggedEntries, byRuleType}] (max 31),
        "topFields", "fieldsAvailable", "groupsAvailable", "examples": [{time, rule, field, value, correctValue,
        group}], "dataSource", "paging" (pages of events; hasMore), "truncated", "verbatim_markdown"}
    """
    try:
        if not monitor_name:
            return {"status": "error", "message": "monitor_name is required"}
        if page < 1 or not 1 <= page_size <= MAX_EXAMPLES:
            return {"status": "error", "message": f"page must be >= 1 and page_size between 1 and {MAX_EXAMPLES}"}
        sub_types = None
        if issue_type:
            sub_types = ISSUE_TYPES.get(issue_type.strip().lower().replace(" ", "_")) or \
                ISSUE_TYPES.get(issue_type.strip().upper())
            if not sub_types:
                return {"status": "error", "message": "Invalid issue_type. Use one of: integrity, consistency, "
                        "schema, " + ", ".join(RULE_IDS)}
        try:
            s, e = _range(start_time, end_time)
            selected = _day_key(day)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid time range or day: {ex}"}
        if isinstance(fields, str):
            fields = [f.strip() for f in fields.split(",") if f.strip()]
        client, err = _client()
        if err:
            return err
        m, failure = await _resolve_dq_monitor(client, monitor_name, owner)
        if failure:
            return _resolve_failure(failure)
        source, failure = _source(m, data_source)
        if failure:
            return failure
        res = await client.get_data_quality(m["userName"], monitor_name, s, e, page, page_size, selected,
                                            fields, sub_types, [group] if group else None, source)
        if res["status"] != "success" or not isinstance(res["data"], dict):
            return _failure(res if res["status"] != "success" else {"status": "empty"})
        filters = {k: v for k, v in {"issue_type": issue_type, "subTypes": sub_types, "fields": fields,
                                     "group": group, "day": day, "dataSource": data_source}.items() if v}
        return _dq_result(res["data"], m, s, e, filters, page, page_size, MAX_ROWS,
                          _cell(data_source.strip()) if data_source else DEFAULT_SOURCE)
    except Exception as ex:
        logger.error(f"Error in aw_get_data_quality: {ex}")
        return {"status": "error", "message": f"Error in aw_get_data_quality: {ex}"}


@mcp_server.tool()
async def aw_get_data_quality_volume(monitor_name: str,
                                     owner: Optional[str] = None,
                                     start_time: Optional[str] = None,
                                     end_time: Optional[str] = None,
                                     group: Optional[str] = None,
                                     data_source: Optional[str] = None) -> Dict[str, Any]:
    """
    Check whether the daily record volume of a DATA_QUALITY monitor in AI WatchTower (AW) is normal.

    Use this tool when the user asks if volume is normal, whether data stopped or spiked, or how many records
    arrived per day compared with the expected baseline. It returns per day: reference, baseline, actual,
    deviation and level (normal, warning, critical), plus the detection mode, sensitivity and thresholds. A day
    with finalized false is still in progress, so its actual count is incomplete. Append `verbatim_markdown`.

    Status: success; no_data (no counts at all in the window); not_found; ambiguous; error.

    Args:
        monitor_name (str): Exact monitor name.
        owner (str, optional): Monitor owner (userName); looked up when omitted.
        start_time (str, optional): "2026-10-05", "3d" (the last 3 days), "24h", "yesterday", "lastweek" or epoch ms. Default: last 7 days (UTC).
            "3d"/"24h" count back from end_time when it is given (at least 1).
        end_time (str, optional): Same formats; a plain date includes that whole day.
        group (str, optional): One group value; default is all groups summed (Total).
        data_source (str, optional): One data source name of the monitor (case-insensitive); an unknown name is
            an error listing the valid ones. Default: AW uses the first data source that has data.

    Returns:
        {"status", "owner", "ownerTimezone", "mode", "sensitivity", "thresholds", "enabled", "days": [{day, reference,
        baseline, actual, deviation, level, finalized}] (max 31), "warningDays", "criticalDays",
        "inProgressDays", "groupsAvailable", "verbatim_markdown"}
    """
    try:
        if not monitor_name:
            return {"status": "error", "message": "monitor_name is required"}
        try:
            s, e = _range(start_time, end_time)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid time range: {ex}"}
        client, err = _client()
        if err:
            return err
        m, failure = await _resolve_dq_monitor(client, monitor_name, owner)
        if failure:
            return _resolve_failure(failure)
        source, failure = _source(m, data_source)
        if failure:
            return failure
        res = await client.get_data_quality_volume(m["userName"], monitor_name, s, e, group, source)
        if res["status"] != "success" or not isinstance(res["data"], dict):
            return _failure(res if res["status"] != "success" else {"status": "empty"})
        d, tz = res["data"], res["data"].get("ownerTimezone")
        points = sorted(d.get("points") or [], key=lambda p: p.get("timestamp", 0))
        rows = [{"day": _day(p["timestamp"]), **{k: p.get(k) for k in ("reference", "baseline", "actual",
                 "deviation", "level", "expectedSource", "finalized")}} for p in points][-MAX_DAYS:]
        flag = lambda pred: [r["day"] for r in rows if pred(r)]
        warn, crit = flag(lambda r: r["level"] == "warning"), flag(lambda r: r["level"] == "critical")
        partial = flag(lambda r: r["finalized"] is False)
        thresholds = _pick(d, ["warningRatio", "criticalRatio", "thresholdsEnabled", "warningBaselineFactor",
                               "criticalBaselineFactor", "hasReference"])
        no_data = all(r["actual"] is None for r in rows)
        msg = ("No record counts exist for this window." if no_data else
               f"{len(crit)} critical and {len(warn)} warning days" + (f"; {', '.join(partial)} still in progress." if partial else "."))
        if d.get("enabled") is False:
            msg += " Volume detection is off for this monitor, so levels are not evaluated."
        num = lambda v: "-" if v is None else f"{v:,.0f}"
        shown_source = _cell(data_source.strip()) if data_source else DEFAULT_SOURCE
        lines = [f"**Data quality volume: {_cell(m.get('monitorName'))}** ({_scope_line(m.get('userName'), tz, s, e, shown_source)}, "
                 f"group: {_cell(group or 'Total')}, mode: {d.get('detectionMode')}, sensitivity: {d.get('sensitivity')})", "", msg, "",
                 "| Day | Reference | Baseline | Actual | Deviation | Level | Final |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['day']} | {num(r['reference'])} | {num(r['baseline'])} | {num(r['actual'])} | "
                  f"{(r['deviation'] or 0) * 100:.1f}% | {r['level']} | {'yes' if r['finalized'] else 'in progress'} |"
                  for r in rows]
        if len(points) > MAX_DAYS:
            lines.append(f"\nShowing the latest {MAX_DAYS} of {len(points)} days.")
        return {"status": "no_data" if no_data else "success", "message": msg, "monitorFound": True,
                "monitorName": _cell(m.get("monitorName")), "owner": _cell(m.get("userName")),
                "scope": {"owner": _cell(m.get("userName"))}, "dataSource": shown_source,
                "ownerTimezone": tz, "timeRange": {"startMs": s, "endMs": e, "start": _iso(s), "end": _iso(e), "timezone": "UTC"},
                "group": _cell(group or "Total"), "enabled": d.get("enabled"), "mode": d.get("detectionMode"),
                "sensitivity": d.get("sensitivity"), "thresholds": thresholds, "days": rows,
                "days_truncated": len(points) > MAX_DAYS, "warningDays": warn, "criticalDays": crit,
                "inProgressDays": partial, "groupsAvailable": [_cell(g) for g in (d.get("groups") or [])[:MAX_LIST]],
                "verbatim_markdown": "\n".join(lines)}
    except Exception as ex:
        logger.error(f"Error in aw_get_data_quality_volume: {ex}")
        return {"status": "error", "message": f"Error in aw_get_data_quality_volume: {ex}"}


@mcp_server.tool()
async def aw_get_data_quality_overview(scope: str = "organization",
                                       start_time: Optional[str] = None,
                                       end_time: Optional[str] = None,
                                       max_monitors: int = 20) -> Dict[str, Any]:
    """
    Rank the DATA_QUALITY monitors in AI WatchTower (AW) by how many flagged entries they have.

    Use this tool when the user asks broadly "what data quality issues do we have", which monitors are worst, or
    wants a data quality overview across monitors. For one monitor's details use aw_get_data_quality. Scope
    "organization" lists every monitor the user's role may see (a role limited to its own monitors sees only
    those); "mine" lists only the user's own. Counts are flagged entries, not distinct bad rows. Monitors are
    taken in name order, at most max_monitors are queried, and the rest are counted as "not shown". A monitor
    that fails is shown with its error and does not fail the call. Append `verbatim_markdown`.

    Args:
        scope (str): "organization" (default) or "mine".
        start_time (str, optional): "2026-10-05", "3d" (the last 3 days), "24h", "yesterday", "lastweek" or epoch ms. Default: last 7 days (UTC).
            "3d"/"24h" count back from end_time when it is given (at least 1).
        end_time (str, optional): Same formats; a plain date includes that whole day.
        max_monitors (int): Monitors to query, 1-50 (default 20).

    Returns:
        {"status": "success" | "no_data" | "no_flagged_entries" | "not_found" | "error", "scope", "timeRange",
        "monitors": [{monitorName, owner, model, version, dataSource, status, totalFlaggedEntries, recordsChecked,
        topRuleType, message}], "monitorsTotal", "notShown", "verbatim_markdown"}
    """
    try:
        scope = (scope or "").lower()
        if scope not in ("mine", "organization") or not 1 <= max_monitors <= 50:
            return {"status": "error", "message": "scope must be 'mine' or 'organization'; max_monitors 1-50"}
        try:
            s, e = _range(start_time, end_time)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid time range: {ex}"}
        client, err = _client()
        if err:
            return err
        res = await client.list_data_quality_monitors(scope)
        if res["status"] != "success" or not isinstance(res["data"], dict):
            return _failure(res if res["status"] != "success" else {"status": "empty"})
        listed = sorted(res["data"].get("monitors") or [],
                        key=lambda m: (str(m.get("monitorName", "")).lower(), str(m.get("userName", "")).lower()))
        total = res["data"].get("totalElements", len(listed))
        if not listed:
            return {"status": "not_found", "scope": scope, "message": f"No data quality monitors are visible with scope '{scope}'."}
        picked = listed[:max_monitors]
        sem = asyncio.Semaphore(OVERVIEW_CONCURRENCY)

        async def one(m):
            async with sem:
                try:
                    r = await client.get_data_quality(m["userName"], m["monitorName"], s, e, 1, 1)
                except Exception as ex:  # one monitor must not sink the rest
                    r = {"status": "error", "message": str(ex)}
            row = {"monitorName": _cell(m.get("monitorName")), "owner": _cell(m.get("userName")),
                   "model": _cell(m["modelName"]) if m.get("modelName") else None, "version": m.get("version"),
                   "dataSource": ", ".join(_cell(d.get("name")) for d in m.get("dataSources") or []) or None}
            if r["status"] != "success" or not isinstance(r["data"], dict):
                return {**row, "status": "error", "message": _failure(r if r["status"] != "success" else {"status": "empty"})["message"]}
            d = _dq_result(r["data"], m, s, e, {}, 1, 1, 0)
            top = max(d["countsByRuleType"].items(), key=lambda kv: kv[1])[0] if d["countsByRuleType"] else None
            return {**row, "status": d["status"], "totalFlaggedEntries": d["totalFlaggedEntries"],
                    "recordsChecked": d["recordsChecked"], "topRuleType": top}

        rows = await asyncio.gather(*(one(m) for m in picked))
        ok = sorted([r for r in rows if r["status"] != "error"], key=lambda r: -r["totalFlaggedEntries"])
        rows = ok + [r for r in rows if r["status"] == "error"]
        not_shown = max(0, max(total, len(listed)) - len(picked))
        flagged = sum(r.get("totalFlaggedEntries", 0) for r in ok)
        status = "success" if flagged else ("no_flagged_entries" if any(r["recordsChecked"] for r in ok) else
                 "no_data" if ok else "error")
        msg = f"{flagged} flagged entries across {len(ok)} of {len(picked)} monitors queried."
        lines = [f"**Data quality overview** (scope: {scope}, window UTC: {_iso(s)} to {_iso(e)}; counts are flagged entries)", "",
                 "| Monitor | Model | Owner | Source | Flagged entries | Top rule type |", "|---|---|---|---|---|---|"]
        for r in rows:
            if r["status"] == "error":
                flagged_cell, top_cell = f"error: {r['message']}", "-"
            else:
                flagged_cell = r["totalFlaggedEntries"] if r["recordsChecked"] else "no data"
                top_cell = r["topRuleType"] or "-"
            lines.append("| " + " | ".join((r["monitorName"], str(r["model"]), r["owner"], str(r["dataSource"]),
                                            _cell(flagged_cell), _cell(top_cell))) + " |")
        if not_shown:
            lines.append(f"\n{not_shown} more not shown (raise max_monitors, up to 50).")
        return {"status": status, "message": msg, "scope": scope,
                "timeRange": {"startMs": s, "endMs": e, "start": _iso(s), "end": _iso(e), "timezone": "UTC"},
                "monitors": rows, "monitorsTotal": max(total, len(listed)), "notShown": not_shown,
                "verbatim_markdown": "\n".join(lines)}
    except Exception as ex:
        logger.error(f"Error in aw_get_data_quality_overview: {ex}")
        return {"status": "error", "message": f"Error in aw_get_data_quality_overview: {ex}"}
