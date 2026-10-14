"""
AI WatchTower (AW) data quality result tools (AW-2390). Coded against the AW external API contract:
AW-2390-data-quality-api-contract.md. Requests carry REAL UTC epoch ms; AW shifts them to the monitor owner's time
zone. Day keys in responses are the owner's local midnight written as UTC ("fake UTC") and are only formatted as plain
dates, never shifted. Words and layouts follow the Workbench page (see aw_labels.py): "Anomalous Data / Total Data",
sections Data Integrity / Data Consistency / Schema Check, daily buckets from the owner's midnight.

Tools:
- aw_get_data_quality: one monitor: data quality, schema check and volume together (Workbench layout) plus examples
- aw_get_data_quality_volume: one monitor: daily volume vs reference and baseline
- aw_get_data_quality_overview: all monitors at a glance: data quality, schema check and volume in one table
"""

import asyncio
import calendar
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from ..server import mcp_server
from .aw_labels import (ISSUE_TYPE_HELP, ISSUE_TYPES, RULE_IDS, SCHEMA_OPTIONS, SECTION_SCHEMA, SECTIONS,
                        display_name, issue_type_ids, num, ordered_types, pct, ratio, sections_by_type, signed,
                        signed_pct)
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
DEFAULT_SOURCE = "default (AW uses the first data source that has data)"
FILTER_NOTE = "The per-type counts and the per-day table cover the whole window and are NOT filtered."
MIXED_NOTE = "A record can appear in more than one type, so the percentages can add up to more than the total."
SECTION_KEYS = ("quality", "schema", "volume")
LEVELS = {"normal": 0, "warning": 1, "critical": 2}


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


def _md(day: str) -> str:
    """The Workbench x-axis label: 10-12."""
    return day[5:]


def _pretty(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d:%b} {d.day}"


# --------------------------------------------------------------------------------------------------- time windows
class Window(NamedTuple):
    start: int          # real UTC epoch ms
    end: int
    whole_days: bool    # AW widens the range to whole owner-local days (midnight to end of day), like the Workbench
    is_default: bool
    label: str


def _noon(date: str) -> int:
    """A date as real UTC noon: lands inside that calendar day for any owner time zone from UTC-12 to UTC+11."""
    return calendar.timegm(time.strptime(date, "%Y-%m-%d")) * 1000 + DAY_MS // 2


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _window(start_time: Optional[str], end_time: Optional[str], now: Optional[int] = None) -> Window:
    """The time window of a question. Default (nothing given): yesterday 00:00 to now, the Workbench's opening range.
    Keywords follow the Workbench presets: today, yesterday, this week (from Sunday), this month, last week (the 7
    days before today), last 24/48/72 hours; also "3d" / "last 3 days" (3 whole days ending today or end_time),
    dates (whole days in the owner's time zone) and epoch ms or date-times (used exactly as given)."""
    now = int(time.time() * 1000) if now is None else int(now)
    st, et = (str(start_time).strip() if start_time else ""), (str(end_time).strip() if end_time else "")
    kw = re.sub(r"[\s_\-]+", "", st.lower())
    if not st and not et:
        return Window(now - DAY_MS, now, True, True, "yesterday and today so far")
    if kw == "today":
        return Window(now, now, True, False, "today")
    if kw == "yesterday":
        return Window(now - DAY_MS, now - DAY_MS, True, False, "yesterday")
    if kw == "thisweek":
        sunday_based = (datetime.fromtimestamp(now / 1000, tz=timezone.utc).weekday() + 1) % 7
        return Window(now - sunday_based * DAY_MS, now, True, False, "this week")
    if kw == "thismonth":
        day = datetime.fromtimestamp(now / 1000, tz=timezone.utc).day
        return Window(now - (day - 1) * DAY_MS, now, True, False, "this month")
    if kw == "lastweek":
        return Window(now - 7 * DAY_MS, now - DAY_MS, True, False, "last week")
    days = re.fullmatch(r"(?:last)?(\d{1,3})(d|days?)", kw)
    hours = re.fullmatch(r"(?:last)?(\d{1,3})(h|hrs?|hours?)", kw)
    if (days or hours) and int((days or hours).group(1)) < 1:
        raise ValueError("the relative amount must be at least 1 (for example '1d' or '24h')")
    if days or hours:
        end = _noon(et) if _DATE.fullmatch(et) else now
        if hours:
            n = int(hours.group(1))
            if n > 366 * 24:
                raise ValueError("the time range cannot be longer than 366 days")
            return Window(end - n * 3600000, end, False, False, f"the last {n} hours")
        n = int(days.group(1))
        if n > 366:
            raise ValueError("the time range cannot be longer than 366 days")
        return Window(end - (n - 1) * DAY_MS, end, True, False, f"the last {n} days")
    if (not st or _DATE.fullmatch(st)) and (not et or _DATE.fullmatch(et)):
        end = _noon(et) if et else now
        start = _noon(st) if st else end - DAY_MS
        if start > end:
            raise ValueError("start_time is after end_time")
        if end - start > 366 * DAY_MS:
            raise ValueError("the time range cannot be longer than 366 days")
        return Window(start, end, True, False, "the dates asked for")
    s, e = parse_time_parameters(start_time, end_time, "UTC")  # epoch ms and date-times, used exactly as given
    e = now if e is None else e
    s = e - DAY_MS if s is None else s
    if s > e:
        raise ValueError("start_time is after end_time")
    if e - s > 366 * DAY_MS:
        raise ValueError("the time range cannot be longer than 366 days")
    return Window(int(s), int(e), False, False, "the time range asked for")


# Java time zone ids that newer tz databases (and some containers) no longer carry as links.
_LEGACY_ZONES = {"US/Eastern": "America/New_York", "US/Central": "America/Chicago", "US/Mountain": "America/Denver",
                 "US/Pacific": "America/Los_Angeles", "US/Alaska": "America/Anchorage", "US/Hawaii": "Pacific/Honolulu",
                 "US/Arizona": "America/Phoenix", "Asia/Calcutta": "Asia/Kolkata", "Asia/Saigon": "Asia/Ho_Chi_Minh"}


def _zone(tz: Optional[str]):
    if not tz or not ZoneInfo:
        return None
    for name in (tz, _LEGACY_ZONES.get(tz)):
        try:
            return ZoneInfo(name) if name else None
        except Exception:
            continue
    return None


def _window_info(win: Window, tz: Optional[str], days: Optional[List[str]] = None) -> Dict[str, Any]:
    """The window as the agent must state it, in the owner's time zone. `days` (the day rows of the response, owner-local
    dates) fill in the first and last day when the time zone name is unknown to this machine."""
    info = {"label": win.label, "isDefault": win.is_default, "wholeDays": win.whole_days,
            "startMs": win.start, "endMs": win.end, "timezone": tz or "UTC"}
    zone = _zone(tz)
    now_ms = int(time.time() * 1000)
    if win.whole_days and zone:
        first = datetime.fromtimestamp(win.start / 1000, zone).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(win.end / 1000, zone).strftime("%Y-%m-%d")
        now = datetime.now(zone)
        info.update(firstDay=first, lastDay=last, includesToday=last >= now.strftime("%Y-%m-%d"),
                    nowLocal=now.strftime("%H:%M"))
    elif win.whole_days and days:  # unknown zone name: the response's own day rows are the owner's dates
        info.update(firstDay=days[0], lastDay=days[-1], includesToday=win.end >= now_ms - DAY_MS // 2, nowLocal=None)
    else:
        info.update(start=_iso(win.start), end=_iso(win.end), timezone="UTC" if not win.whole_days else (tz or "UTC"))
    return info


def _response_days(dq_res, vol_res) -> List[str]:
    """Owner-local dates (oldest first) present in an AW data quality or volume response."""
    if dq_res is not None:
        return [r["day"] for r in _digest(dq_res["data"]).days]
    if vol_res is not None:
        return sorted(_day(p["timestamp"]) for p in vol_res["data"].get("points") or [] if p.get("timestamp"))
    return []


def _in_progress_day(info: Dict[str, Any]) -> Optional[str]:
    return info.get("lastDay") if info.get("includesToday") else None


def _opening(info: Dict[str, Any], scope_text: str, extra: str = "") -> str:
    """The first sentence of the answer: what window was used and how to ask for another."""
    if info.get("firstDay"):
        first, last = info["firstDay"], info["lastDay"]
        live = " today is still in progress" if info.get("includesToday") else ""
        if info["isDefault"]:
            until = f"{_pretty(last)} {info['nowLocal']}" if info.get("nowLocal") else f"{_pretty(last)} now"
            span = (f"yesterday and today so far ({_pretty(first)} 00:00 to {until}, "
                    f"{info['timezone']}; two days,{live or ' whole days'})")
        elif first == last:
            span = f"{_pretty(first)} ({info['timezone']}; whole day{',' + live if live else ''})"
        else:
            span = f"{_pretty(first)} to {_pretty(last)} ({info['timezone']}; whole days{',' + live if live else ''})"
    else:
        span = f"{info['start']} to {info['end']} UTC"
    ask = (' Tell me a date or range (for example "last week" or "Oct 6 to Oct 13") if you need a different period.'
           if info["isDefault"] else "")
    return f"Showing {scope_text} for {span}.{ask}{extra}"


def _window_text(info: Dict[str, Any]) -> str:
    """The window in one phrase for table captions, so it is always shown with the numbers."""
    if info.get("firstDay"):
        first, last = info["firstDay"], info["lastDay"]
        span = _pretty(first) if first == last else f"{_pretty(first)} to {_pretty(last)}"
        live = ", today in progress" if info.get("includesToday") else ""
        head = "yesterday and today so far: " if info["isDefault"] else ""
        return f"window: {head}{span} ({info['timezone']}, whole days{live})"
    return f"window: {info['start']} to {info['end']} UTC"


def _whole_days(start_time: Optional[str]) -> bool:
    return _window(start_time, None).whole_days


# ------------------------------------------------------------------------------------------------------ plumbing
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


def _resolve_failure(failure: Dict[str, Any]) -> Dict[str, Any]:
    return _failure(failure) if failure.get("http_status") else failure


def _day_key(day: Optional[str]) -> Optional[int]:
    if day is None or day == "":
        return None
    d = str(day).strip()
    if _DATE.fullmatch(d):  # owner-local midnight written as UTC, same as AW's keys
        return calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
    if re.fullmatch(r"\d{10,13}", d):  # a day key copied from an earlier result, sent back verbatim
        return int(d)
    raise ValueError("day must be YYYY-MM-DD or a day key (epoch ms) from an earlier result")


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


def _example(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Trimmed records: one per info item (a flagged field), keys per contract 1.2."""
    side = ev.get("target") or ev.get("source") or {}
    out = []
    for i in side.get("info") or [{}]:
        kind = i.get("subTypeStr") or i.get("anomalyType") or ev.get("dataQualityType")
        out.append({"time": _iso(ev["timestamp"]) if ev.get("timestamp") else None,
                    "type": _cell(display_name(kind)) if kind else None,
                    "field": _cell(i["key"]) if i.get("key") is not None else None,
                    "value": _cell(i["value"], 80) if i.get("value") is not None else None,
                    "correctValue": _cell(i["correctValue"], 80) if i.get("correctValue") is not None else None,
                    "group": _cell(i["groupName"]) if i.get("groupName") is not None else None})
    return out


# --------------------------------------------------------------------------------------------- data quality digest
class Digest(NamedTuple):
    days: List[Dict[str, Any]]      # [{day, dayKey, totalData, byType, items}] oldest first
    sub_type_map: Dict[str, int]
    total_items: int                # AW's totalAnomalyCount (filtered when a filter is set)
    total_data: int                 # records checked in the whole window
    by_type: Dict[str, int]         # whole-window items per type name (UIE's names)


def _digest(data: Dict[str, Any]) -> Digest:
    rows = []
    for key, v in (data.get("subTypeDistribution") or {}).items():
        by_type = {k: int(c) for k, c in ((v or {}).get("distribution") or {}).items()}
        rows.append({"day": _day(key), "dayKey": int(key), "totalData": int((v or {}).get("totalCount") or 0),
                     "byType": by_type, "items": sum(by_type.values())})
    rows.sort(key=lambda r: r["dayKey"])
    checked = data.get("recordsChecked")
    checked = sum(r["totalData"] for r in rows) if checked is None else int(checked)
    by_type: Dict[str, int] = {}
    for r in rows:
        for k, c in r["byType"].items():
            by_type[k] = by_type.get(k, 0) + c
    return Digest(rows, data.get("subTypeMap") or {}, int(data.get("totalAnomalyCount") or 0), checked, by_type)


def _names_in(dg: Digest, section_schema: bool) -> List[str]:
    """Type names with data, in Workbench order, for the schema section or for the integrity/consistency ones."""
    sec = sections_by_type(dg.sub_type_map, dg.by_type)
    return ordered_types([n for n, c in dg.by_type.items() if c and (sec[n] == SECTION_SCHEMA) == section_schema],
                         dg.sub_type_map)


def _type_table(dg: Digest, names: List[str], in_progress: Optional[str]) -> List[str]:
    """Tooltip-style table for one day, a day-by-type matrix for several days (the Workbench chart as text)."""
    sec = sections_by_type(dg.sub_type_map, names)
    days = dg.days[-MAX_DAYS:]
    if len(days) == 1:
        d = days[0]
        lines = [f"Anomalous Data / Total Data, {_md(d['day'])}" + (" (in progress)" if d["day"] == in_progress else ""),
                 "", "| Section | Type | Anomalous / Total | % |", "|---|---|---|---|"]
        lines += [f"| {sec[n]} | {_cell(display_name(n))} | {num(d['byType'].get(n, 0))} / {num(d['totalData'])} | "
                  f"{pct(d['byType'].get(n, 0), d['totalData'])} |" for n in names]
        return lines + ["", f"Total {num(sum(d['byType'].get(n, 0) for n in names))} items"]
    head = ["Day", "Total data"] + [_cell(display_name(n)) for n in names] + ["Total items"]
    lines = ["Anomalous Data / Total Data (each cell: anomalous count and its share of that day's total data)", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for d in days:
        cells = [f"{num(d['byType'].get(n, 0))} ({pct(d['byType'].get(n, 0), d['totalData'])})" if d["byType"].get(n)
                 else "0" for n in names]
        lines.append(f"| {_md(d['day'])}{' (in progress)' if d['day'] == in_progress else ''} | {num(d['totalData'])} | "
                     + " | ".join(cells) + f" | {num(sum(d['byType'].get(n, 0) for n in names))} |")
    if len(dg.days) > MAX_DAYS:
        lines.append(f"\nShowing the latest {MAX_DAYS} of {len(dg.days)} days.")
    groups = {}
    for n in names:
        groups.setdefault(sec[n], []).append(display_name(n))
    return lines + ["", " ".join(f"{s}: {', '.join(groups[s])}." for s in SECTIONS if s in groups)]


def _schema_line(m: Dict[str, Any]) -> str:
    s = m.get("monitorSetting") or {}
    if not s.get("schemaCheckEnabled"):
        return "Schema Check: Disabled (this monitor does not check the schema)."
    kinds = [SCHEMA_OPTIONS.get(k, k) for k, v in (s.get("schemaCheck") or {}).items() if v]
    return "Schema Check: Enabled" + (f" ({', '.join(kinds)})." if kinds else ".")


def _quality_parts(dg: Digest, m: Dict[str, Any], win_info: Dict[str, Any], filters: Dict[str, Any],
                   show_schema: bool, show_quality: bool) -> Dict[str, Any]:
    """Status, message and markdown lines for the data quality and schema check sections of one monitor."""
    total, checked = dg.total_items, dg.total_data
    active = {k: v for k, v in filters.items() if k in ("issue_type", "fields", "group", "day")}
    desc = ", ".join(f"{k}={_cell(','.join(v) if isinstance(v, list) else v)}" for k, v in active.items())
    if checked == 0:
        status = "no_data"
        msg = "No data was checked in this window (no records arrived, or no rules are active). This is not the same as healthy."
    elif total == 0:
        status = "no_anomalous_data"
        msg = (f"No anomalous data matches this filter ({num(checked)} total data were checked in the window)." if active
               else f"{num(checked)} total data were checked and no anomalous data was found.")
    else:
        status = "success"
        msg = (f"{num(total)} items of anomalous data match your filter ({desc}). {FILTER_NOTE}" if active else
               f"{num(total)} items of anomalous data in {num(checked)} total data.")
    in_progress = _in_progress_day(win_info)
    lines: List[str] = []
    if show_quality:
        names = _names_in(dg, False)
        lines += ["Per-day counts for the whole window (not filtered):", ""] if active and names else []
        lines += (_type_table(dg, names, in_progress) + ["", MIXED_NOTE]) if names else \
            ["Anomalous Data / Total Data: none in this window." if checked else "No data was checked in this window."]
    if show_schema:
        names = _names_in(dg, True)
        lines += ["", _schema_line(m).replace("Schema Check:", "**Schema Check:**", 1)]
        lines += ([""] + _type_table(dg, names, in_progress)) if names else \
            (["", "No schema anomalies in this window."] if checked else [])
    return {"status": status, "message": msg, "lines": lines}


# ------------------------------------------------------------------------------------------------------- volume
def _volume_rows(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for p in sorted(points, key=lambda p: p.get("timestamp", 0))[-MAX_DAYS:]:
        final = p.get("finalized") is not False
        actual, ref, base = p.get("actual"), p.get("reference"), p.get("baseline")

        def vs(expected, dev_key):
            if not final or actual is None or expected is None:
                return None
            frac = p.get(dev_key) if isinstance(p.get(dev_key), (int, float)) else ((actual - expected) / expected if expected else 0)
            return {"difference": actual - expected, "fraction": frac}
        rows.append({"day": _day(p["timestamp"]), "reference": ref, "baseline": base, "actual": actual,
                     "level": "in_processing" if not final else p.get("level"), "finalized": final,
                     "vsReference": vs(ref, "referenceDeviation"), "vsBaseline": vs(base, "baselineDeviation")})
    return rows


def _volume_lines(rows: List[Dict[str, Any]]) -> List[str]:
    cell = lambda v: "-" if v is None else num(v)
    cmp = lambda v: "-" if not v else f"{signed(v['difference'])} ({signed_pct(v['fraction'])})"
    level = lambda r: "In processing" if r["level"] == "in_processing" else str(r["level"] or "-").capitalize()
    if not rows:
        return []
    lines = ["| Day | Reference Volume | Baseline Volume | Actual Volume | vs Reference | vs Baseline | Level |",
             "|---|---|---|---|---|---|---|"]
    return lines + [f"| {_md(r['day'])} | {cell(r['reference'])} | {cell(r['baseline'])} | {cell(r['actual'])} | "
                    f"{cmp(r['vsReference'])} | {cmp(r['vsBaseline'])} | {level(r)} |" for r in rows]


def _volume_parts(d: Dict[str, Any]) -> Dict[str, Any]:
    rows = _volume_rows(d.get("points") or [])
    flag = lambda lv: [r["day"] for r in rows if r["level"] == lv]
    warn, crit, partial = flag("warning"), flag("critical"), flag("in_processing")
    no_data = not rows or all(r["actual"] is None for r in rows)
    msg = ("No volume counts exist for this window." if no_data else
           f"{len(crit)} critical and {len(warn)} warning days" + (f"; {', '.join(partial)} in processing." if partial else "."))
    if d.get("enabled") is False:
        msg += " Volume detection is off for this monitor, so levels are not evaluated."
    thresholds = _pick(d, ["warningRatio", "criticalRatio", "thresholdsEnabled", "warningBaselineFactor",
                           "criticalBaselineFactor", "hasReference"])
    return {"rows": rows, "noData": no_data, "message": msg, "warningDays": warn, "criticalDays": crit,
            "inProcessingDays": partial, "thresholds": thresholds, "enabled": d.get("enabled"),
            "mode": d.get("detectionMode"), "sensitivity": d.get("sensitivity")}


def _worst_level(rows: List[Dict[str, Any]]) -> str:
    """One word for the overview table: the worst final level, with its day; In processing when nothing is final."""
    # a day with no counts at all (no actual, or zero with nothing expected) has no level to show
    rows = [r for r in rows if r["actual"] is not None and (r["actual"] or r["baseline"] or r["reference"])]
    final = [r for r in rows if r["level"] in LEVELS]
    if not final:
        return "In processing" if rows else "-"
    worst = max(final, key=lambda r: LEVELS[r["level"]])
    if worst["level"] == "normal":
        return "Normal"
    ref = worst.get("baseline") or worst.get("reference")
    side = "" if not ref or worst["actual"] == ref else (", above baseline" if worst["actual"] > ref else ", below baseline")
    return f"{worst['level'].capitalize()} on {_md(worst['day'])}{side}"


# ------------------------------------------------------------------------------------------------------- tools
def _sections(value) -> Optional[List[str]]:
    if value is None:
        return None
    items = [v.strip().lower() for v in (value.split(",") if isinstance(value, str) else value) if str(v).strip()]
    bad = [v for v in items if v not in SECTION_KEYS]
    if bad or not items:
        raise ValueError("sections must be a list of: " + ", ".join(SECTION_KEYS))
    return items


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
                              page_size: int = 10,
                              sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Report on ONE named DATA_QUALITY monitor in AI WatchTower (AW): data quality, schema check and volume together,
    in the Workbench's words and layout.

    Use this tool when the user names a monitor: "how is X doing", "what data quality issues does X have", which
    types or fields fail, example records, whether X's schema check finds anything, or whether X's volume is normal.
    Do NOT use it when no monitor is named (use aw_get_data_quality_overview), for the rules and settings of a
    monitor (aw_get_data_quality_config), or for why something failed (no tool here explains causes).

    Words (use exactly these): "Anomalous Data / Total Data" is the number of items with a problem over the number of
    records checked (total data); "Total N items" is the sum over the types; sections are Data Integrity, Data
    Consistency and Schema Check; types are Missing Data, Data Outliers, Field Type Mismatch, Disqualified Data Range,
    Custom, Inconsistency (and the schema types). Never say "flagged entries". The default window is yesterday 00:00
    to now in the owner's time zone; a day always runs from 00:00. Begin your answer with the `opening` sentence, then
    a short summary, then append `verbatim_markdown` as it is (do not retype the tables).

    Status: success; no_anomalous_data (data was checked, none anomalous); no_data (nothing was checked in the
    window: say so, it is not the same as healthy); not_found (the result lists `closestNames`); ambiguous (several
    owners, ask which); error.

    Args:
        monitor_name (str): Exact monitor name.
        owner (str, optional): Monitor owner (userName); looked up when omitted.
        start_time (str, optional): today, yesterday, this week (from Sunday), this month, last week, "3d" / "last 3 days"
            (3 whole days ending today), a date "2026-10-05" (whole days in the owner's time zone), epoch ms, or
            "24h" (hours are used exactly). Default: yesterday and today so far.
        end_time (str, optional): A date or epoch ms; a plain date includes that whole day.
        data_source (str, optional): One data source name of the monitor (case-insensitive); an unknown name is
            an error listing the valid ones. Default: AW uses the first data source that has data.
        fields (list[str], optional): Only these fields.
        issue_type (str, optional): integrity, consistency, schema, or one type: Missing Data, Data Outliers,
            Field Type Mismatch, Disqualified Data Range, Custom, Inconsistency, Field Order Mismatch,
            Unexpected Field, Lost Field.
        group (str, optional): Only this group value.
        day (str, optional): Drill into one day: "YYYY-MM-DD" (owner-local date) or a day key from an earlier result.
        page (int): Page of example events, from 1.
        page_size (int): Events per page, 1-50 (default 10). At most 10 rows per event and 100 in total are shown.
        sections (list[str], optional): any of quality, schema, volume. Default: all three, or only quality when a
            filter (issue_type, fields, group, day) is set.

    When a filter is set, only the total items and the examples are filtered; the per-day table covers the whole window.

    Returns:
        {"status", "message", "opening", "window", "owner", "ownerTimezone", "totalData", "totalItems", "countsByType",
        "daily": [{day, dayKey, totalData, items, byType}], "schemaCheck", "volume", "topFields", "fieldsAvailable",
        "groupsAvailable", "examples": [{time, type, field, value, correctValue, group}], "paging", "truncated",
        "verbatim_markdown"}
    """
    try:
        if not monitor_name:
            return {"status": "error", "message": "monitor_name is required"}
        if page < 1 or not 1 <= page_size <= MAX_EXAMPLES:
            return {"status": "error", "message": f"page must be >= 1 and page_size between 1 and {MAX_EXAMPLES}"}
        sub_types = None
        if issue_type:
            sub_types = issue_type_ids(issue_type)
            if not sub_types:
                return {"status": "error", "message": "Invalid issue_type. Use one of: " + ISSUE_TYPE_HELP}
        try:
            win = _window(start_time, end_time)
            selected = _day_key(day)
            secs = _sections(sections)
        except ValueError as ex:
            return {"status": "error", "message": f"Invalid time range, day or sections: {ex}"}
        filtered = bool(sub_types or fields or group or day)
        secs = secs or (["quality"] if filtered else list(SECTION_KEYS))
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
        setting = m.get("monitorSetting") or {}
        want_dq = "quality" in secs or "schema" in secs
        want_volume = "volume" in secs and setting.get("volumeDetectionEnabled") is not False
        calls = []
        if want_dq:
            calls.append(client.get_data_quality(m["userName"], monitor_name, win.start, win.end, page, page_size,
                                                 selected, fields, sub_types, [group] if group else None, source,
                                                 win.whole_days))
        if want_volume:
            calls.append(client.get_data_quality_volume(m["userName"], monitor_name, win.start, win.end, group, source,
                                                        win.whole_days))
        if not calls:
            return {"status": "no_data", "monitorName": _cell(m.get("monitorName")), "owner": _cell(m.get("userName")),
                    "message": "Volume detection is off for this monitor, so there is no volume to show."}
        results = await asyncio.gather(*calls)
        dq_res = results[0] if want_dq else None
        vol_res = results[-1] if want_volume else None
        bad = lambda r: r["status"] != "success" or not isinstance(r["data"], dict)
        if dq_res is not None and bad(dq_res):
            return _failure(dq_res if dq_res["status"] != "success" else {"status": "empty"})
        vol_error = None
        if vol_res is not None and bad(vol_res):
            if dq_res is None:
                return _failure(vol_res if vol_res["status"] != "success" else {"status": "empty"})
            vol_error = _failure(vol_res if vol_res["status"] != "success" else {"status": "empty"})["message"]
            vol_res = None  # the volume section degrades; the data quality part is still good
        name = _cell(m.get("monitorName"))
        shown_source = _cell(data_source.strip()) if data_source else DEFAULT_SOURCE
        tz = (dq_res or vol_res)["data"].get("ownerTimezone")
        info = _window_info(win, tz, _response_days(dq_res, vol_res))
        scope_text = f'monitor "{name}" (owner: {_cell(m.get("userName"))})'
        res: Dict[str, Any] = {"monitorFound": True, "monitorName": name, "owner": _cell(m.get("userName")),
                               "scope": {"owner": _cell(m.get("userName"))}, "ownerTimezone": tz, "dataSource": shown_source,
                               "window": info, "sections": secs, "opening": _opening(info, scope_text)}
        lines = [f"*{res['opening']}*", "",
                 f"**Data quality: {name}** (owner: {_cell(m.get('userName'))}, {_window_text(info)}, data source: {shown_source})", ""]
        status, msgs = "success", []
        if dq_res is not None:
            data = dq_res["data"]
            dg = _digest(data)
            filters = {k: v for k, v in {"issue_type": issue_type, "subTypes": sub_types, "fields": fields,
                                         "group": group, "day": day, "dataSource": data_source}.items() if v}
            q = _quality_parts(dg, m, info, filters, "schema" in secs, "quality" in secs)
            status = q["status"]
            msgs.append(q["message"])
            lines += q["lines"]
            events = data.get("events") or []
            examples, top, more = [], {}, 0
            for ev in events:
                rows = _example(ev)
                more += max(0, len(rows) - MAX_ROWS_PER_EVENT)
                for ex in rows[:MAX_ROWS_PER_EVENT]:
                    if len(examples) < MAX_ROWS:
                        examples.append(ex)
                    else:
                        more += 1
                    if ex["field"]:
                        top[ex["field"]] = top.get(ex["field"], 0) + 1
            fields_av = sorted(_cell(f) for f in data.get("fields") or [])
            groups_av = sorted(_cell(g) for g in data.get("groups") or [])
            total = dg.total_items
            res.update(
                status=status, totalData=dg.total_data, totalItems=total,
                countsByType={display_name(k): v for k, v in dg.by_type.items()},
                daily=[{"day": r["day"], "dayKey": r["dayKey"], "totalData": r["totalData"], "items": r["items"],
                        "byType": {display_name(k): v for k, v in r["byType"].items()}} for r in dg.days[-MAX_DAYS:]],
                daily_truncated=len(dg.days) > MAX_DAYS,
                schemaCheck={"enabled": bool(setting.get("schemaCheckEnabled")), "text": _schema_line(m)},
                topFields=[{"field": f, "itemsOnPage": c} for f, c in sorted(top.items(), key=lambda kv: -kv[1])][:MAX_LIST],
                topFieldsBasis="records on this page only",
                fieldsAvailable=fields_av[:MAX_LIST], total_fields_available=len(fields_av),
                groupsAvailable=groups_av[:MAX_LIST], total_groups_available=len(groups_av), examples=examples,
                paging={"page": page, "pageSize": page_size, "unit": "events", "totalItems": total,
                        "totalPages": max(1, -(-total // page_size)),
                        "hasMore": len(events) == page_size and page * page_size < total},
                truncated=len(dg.days) > MAX_DAYS or len(fields_av) > MAX_LIST or len(groups_av) > MAX_LIST or more > 0)
            if more:
                res["examplesNote"] = f"and {more} more issue rows not shown (at most {MAX_ROWS_PER_EVENT} per event, {MAX_ROWS} in total)"
            if filters and any(k in filters for k in ("issue_type", "fields", "group", "day")):
                res.update(filtersActive=True, note=FILTER_NOTE)
            if examples and "quality" in secs:
                tail = [f"Example items (page {page} of events, {page_size} events per page):", "",
                        "| Time | Data Quality Issue Type | Field | Value | Correct value | Group |", "|---|---|---|---|---|---|"]
                dash = lambda v: "-" if v is None else v
                tail += [f"| {dash(x['time'])} | {dash(x['type'])} | {dash(x['field'])} | {dash(x['value'])} | "
                         f"{dash(x['correctValue'])} | {dash(x['group'])} |" for x in examples]
                lines += [""] + tail
        if "volume" in secs:
            lines += ["", "**Volume**" + (f" (mode: {vol_res['data'].get('detectionMode')}, sensitivity: "
                                          f"{vol_res['data'].get('sensitivity')}, group: {_cell(group or 'Total')})" if vol_res else "")]
            if vol_error:
                lines += ["", f"Volume could not be loaded: {vol_error}"]
                res["volume"] = {"error": vol_error}
            elif vol_res is None:
                lines += ["", "Volume detection is off for this monitor, so the volume is not evaluated."]
                res["volume"] = {"enabled": False}
            else:
                v = _volume_parts(vol_res["data"])
                lines += ["", v["message"], ""] + _volume_lines(v["rows"])
                res["volume"] = {k: v[k] for k in ("enabled", "mode", "sensitivity", "thresholds", "warningDays",
                                                   "criticalDays", "inProcessingDays", "message")}
                res["volume"]["days"] = v["rows"]
                msgs.append("Volume: " + v["message"])
                if dq_res is None:
                    status = "no_data" if v["noData"] else "success"
        res.update(status=status, message=" ".join(msgs) or "Done.", verbatim_markdown="\n".join(lines))
        return res
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
    Volume of ONE named DATA_QUALITY monitor in AI WatchTower (AW): is the daily record volume normal?

    Use this tool when the user names a monitor and asks only about volume: whether data stopped or spiked, how many
    records arrived per day compared with the Reference Volume and the Baseline Volume, a group's volume, or many days.
    Do NOT use it when no monitor is named (use aw_get_data_quality_overview, which has a Volume column) or for
    anomalous data and schema (use aw_get_data_quality). It returns per day the Reference Volume, Baseline Volume,
    Actual Volume, how far Actual is above or below each, and the Level: Normal, Warning, Critical, or In processing
    (the day is not over, so its Actual Volume is incomplete). Use exactly these words. Begin your answer with the
    `opening` sentence, then a short summary, then append `verbatim_markdown` as it is.

    Status: success; no_data (no counts at all in the window); not_found (lists `closestNames`); ambiguous; error.

    Args:
        monitor_name (str): Exact monitor name.
        owner (str, optional): Monitor owner (userName); looked up when omitted.
        start_time (str, optional): today, yesterday, this week, this month, last week, "3d" / "last 3 days", a date
            "2026-10-05", epoch ms or "24h". Default: yesterday and today so far (the owner's days start at 00:00).
        end_time (str, optional): A date or epoch ms; a plain date includes that whole day.
        group (str, optional): One group value; default is all groups summed (Total).
        data_source (str, optional): One data source name of the monitor (case-insensitive); an unknown name is
            an error listing the valid ones. Default: AW uses the first data source that has data.

    Returns:
        {"status", "message", "opening", "window", "owner", "ownerTimezone", "mode", "sensitivity", "thresholds",
        "enabled", "days": [{day, reference, baseline, actual, level, finalized, vsReference, vsBaseline}] (max 31),
        "warningDays", "criticalDays", "inProcessingDays", "groupsAvailable", "verbatim_markdown"}
    """
    try:
        if not monitor_name:
            return {"status": "error", "message": "monitor_name is required"}
        try:
            win = _window(start_time, end_time)
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
        res = await client.get_data_quality_volume(m["userName"], monitor_name, win.start, win.end, group, source,
                                                   win.whole_days)
        if res["status"] != "success" or not isinstance(res["data"], dict):
            return _failure(res if res["status"] != "success" else {"status": "empty"})
        d, tz = res["data"], res["data"].get("ownerTimezone")
        info = _window_info(win, tz, sorted(_day(p["timestamp"]) for p in d.get("points") or [] if p.get("timestamp")))
        v = _volume_parts(d)
        name = _cell(m.get("monitorName"))
        shown_source = _cell(data_source.strip()) if data_source else DEFAULT_SOURCE
        opening = _opening(info, f'monitor "{name}" (owner: {_cell(m.get("userName"))})')
        lines = [f"*{opening}*", "",
                 f"**Volume: {name}** (owner: {_cell(m.get('userName'))}, {_window_text(info)}, data source: {shown_source}, "
                 f"group: {_cell(group or 'Total')}, mode: {d.get('detectionMode')}, sensitivity: {d.get('sensitivity')})", "",
                 v["message"], ""] + _volume_lines(v["rows"])
        if len(d.get("points") or []) > MAX_DAYS:
            lines.append(f"\nShowing the latest {MAX_DAYS} of {len(d['points'])} days.")
        return {"status": "no_data" if v["noData"] else "success", "message": v["message"], "monitorFound": True,
                "opening": opening, "window": info,
                "monitorName": name, "owner": _cell(m.get("userName")), "scope": {"owner": _cell(m.get("userName"))},
                "dataSource": shown_source, "ownerTimezone": tz, "group": _cell(group or "Total"),
                "enabled": v["enabled"], "mode": v["mode"], "sensitivity": v["sensitivity"], "thresholds": v["thresholds"],
                "days": v["rows"], "days_truncated": len(d.get("points") or []) > MAX_DAYS,
                "warningDays": v["warningDays"], "criticalDays": v["criticalDays"],
                "inProcessingDays": v["inProcessingDays"],
                "groupsAvailable": [_cell(g) for g in (d.get("groups") or [])[:MAX_LIST]],
                "verbatim_markdown": "\n".join(lines)}
    except Exception as ex:
        logger.error(f"Error in aw_get_data_quality_volume: {ex}")
        return {"status": "error", "message": f"Error in aw_get_data_quality_volume: {ex}"}


@mcp_server.tool()
async def aw_get_data_quality_overview(scope: str = "organization",
                                       model_name: Optional[str] = None,
                                       start_time: Optional[str] = None,
                                       end_time: Optional[str] = None,
                                       max_monitors: int = 20) -> Dict[str, Any]:
    """
    All DATA_QUALITY monitors in AI WatchTower (AW) at a glance: data quality, schema check and volume in one table.

    Use this tool when NO monitor is named: "what data quality issues do we have", "is volume normal", "what about the
    schema check", "which monitors have problems", or a question about one model's monitors (model_name). One call
    answers all three topics: pick the column the user asked about for your summary. Do NOT use it when a monitor is
    named (use aw_get_data_quality). Table columns: Monitor, Model, Owner, Anomalous Data / Total Data (the items with
    a problem over the records checked), Top type, Schema Check (Enabled with its anomalous / total, or Disabled) and
    Volume (the worst Level in the window: Normal, Warning, Critical, In processing, or Off). Use exactly these words;
    never say "flagged entries". Rows are ranked by items, worst first. Scope "organization" lists every monitor the
    user's role may see; "mine" only the user's own. Monitors are taken in name order, at most max_monitors are
    queried and the rest are counted as "not shown". One monitor that fails shows its error and does not fail the call.
    Never say something applies to "all monitors" unless the table covers all of them.

    Begin your answer with the `opening` sentence (it states the window and how to ask for another), then a short
    summary of the topic asked, then append `verbatim_markdown` as it is. End by inviting the user to name a monitor
    for its data quality, schema check and volume.

    Args:
        scope (str): "organization" (default) or "mine".
        model_name (str, optional): Only the monitors of this model (case-insensitive).
        start_time (str, optional): today, yesterday, this week, this month, last week, "3d" / "last 3 days", a date
            "2026-10-05", epoch ms or "24h". Default: yesterday and today so far (each owner's days start at 00:00).
        end_time (str, optional): A date or epoch ms; a plain date includes that whole day.
        max_monitors (int): Monitors to query, 1-50 (default 20).

    Returns:
        {"status": "success" | "no_data" | "no_anomalous_data" | "not_found" | "error", "message", "opening", "window",
        "scope", "monitors": [{monitorName, owner, model, version, dataSource, status, totalItems, totalData,
        topType, schemaCheck, volume, message}], "monitorsTotal", "notShown", "verbatim_markdown"}
    """
    try:
        scope = (scope or "").lower()
        if scope not in ("mine", "organization") or not 1 <= max_monitors <= 50:
            return {"status": "error", "message": "scope must be 'mine' or 'organization'; max_monitors 1-50"}
        try:
            win = _window(start_time, end_time)
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
        if model_name:
            listed = [m for m in listed if str(m.get("modelName", "")).lower() == model_name.strip().lower()]
        total = max(res["data"].get("totalElements", len(listed)), len(listed)) if not model_name else len(listed)
        if not listed:
            return {"status": "not_found", "scope": scope, "message": f"No data quality monitors are visible with scope '{scope}'"
                    + (f" for model '{_cell(model_name)}'" if model_name else "") + "."}
        picked = listed[:max_monitors]
        sem = asyncio.Semaphore(OVERVIEW_CONCURRENCY)
        zones: List[str] = []
        seen_days: List[str] = []

        async def one(m):
            row = {"monitorName": _cell(m.get("monitorName")), "owner": _cell(m.get("userName")),
                   "model": _cell(m["modelName"]) if m.get("modelName") else None, "version": m.get("version"),
                   "dataSource": ", ".join(_cell(d.get("name")) for d in m.get("dataSources") or []) or None}
            setting = m.get("monitorSetting") or {}
            volume_on = setting.get("volumeDetectionEnabled") is not False
            async with sem:
                try:
                    dq_r, vol_r = await asyncio.gather(
                        client.get_data_quality(m["userName"], m["monitorName"], win.start, win.end, 1, 1,
                                                whole_days=win.whole_days),
                        client.get_data_quality_volume(m["userName"], m["monitorName"], win.start, win.end,
                                                       whole_days=win.whole_days) if volume_on else asyncio.sleep(0))
                except Exception as ex:  # one monitor must not sink the rest
                    dq_r, vol_r = {"status": "error", "message": str(ex)}, None
            if dq_r["status"] != "success" or not isinstance(dq_r["data"], dict):
                return {**row, "status": "error", "message": _failure(dq_r if dq_r["status"] != "success" else {"status": "empty"})["message"]}
            dg = _digest(dq_r["data"])
            if dq_r["data"].get("ownerTimezone"):
                zones.append(dq_r["data"]["ownerTimezone"])
            if dg.days and not seen_days:
                seen_days.extend(r["day"] for r in dg.days)
            sec = sections_by_type(dg.sub_type_map, dg.by_type)
            schema_items = sum(c for n, c in dg.by_type.items() if sec[n] == SECTION_SCHEMA)
            quality = {n: c for n, c in dg.by_type.items() if sec[n] != SECTION_SCHEMA and c}
            items = sum(quality.values())
            top = display_name(max(quality.items(), key=lambda kv: kv[1])[0]) if quality else None
            schema_on = bool(setting.get("schemaCheckEnabled"))
            vol = "Off"
            if volume_on:
                vol = ("error" if not isinstance(vol_r, dict) or vol_r.get("status") != "success"
                       or not isinstance(vol_r.get("data"), dict) else _worst_level(_volume_rows(vol_r["data"].get("points") or [])))
            return {**row, "status": "no_data" if not dg.total_data else ("success" if items else "no_anomalous_data"),
                    "totalItems": items, "totalData": dg.total_data, "topType": top,
                    "schemaCheck": ("Enabled, " + ratio(schema_items, dg.total_data)) if schema_on and dg.total_data
                    else ("Enabled" if schema_on else "Disabled"), "schemaItems": schema_items, "volume": vol}

        rows = await asyncio.gather(*(one(m) for m in picked))
        ok = sorted([r for r in rows if r["status"] != "error"],
                    key=lambda r: (-r["totalItems"], r["totalData"] == 0, r["monitorName"].lower()))
        rows = ok + [r for r in rows if r["status"] == "error"]
        not_shown = max(0, total - len(picked))
        items_all = sum(r.get("totalItems", 0) for r in ok)
        data_all = sum(r.get("totalData", 0) for r in ok)
        status = "success" if items_all else ("no_anomalous_data" if data_all else "no_data" if ok else "error")
        tz = zones[0] if zones else None
        info = _window_info(win, tz, seen_days)
        if len(set(zones)) > 1:
            info["note"] = "Monitors have different owner time zones; each monitor's days start at its owner's 00:00."
        scope_text = ("the monitors of model " + _cell(model_name)) if model_name else \
            ("all data quality monitors in the organization" if scope == "organization" else "your data quality monitors")
        scope_text += f" ({len(picked)} of {total} queried)" if not_shown else f" ({len(picked)})"
        top_name = next((r["monitorName"] for r in ok if r["totalItems"]), None)
        extra = f' Name a monitor (for example "{top_name}") and I will show its data quality, schema check and volume.' if top_name \
            else " Name a monitor and I will show its data quality, schema check and volume."
        n_nodata = sum(1 for r in ok if not r["totalData"])
        msg = (f"{num(items_all)} items of anomalous data in {num(data_all)} total data across {len(ok)} of {len(picked)} monitors queried; "
               f"{len(ok) - n_nodata} monitors had data checked and {n_nodata} had no data in this window"
               + (f", {len(rows) - len(ok)} failed." if len(rows) > len(ok) else ".")
               if data_all else f"No data was checked in this window for any of the {len(picked)} monitors queried.")
        opening = _opening(info, scope_text, extra)
        lines = [f"*{opening}*", "", f"**Data quality overview** ({scope}, {_window_text(info)})", "",
                 "| Monitor | Model | Owner | Anomalous Data / Total Data | Top type | Schema Check | Volume |",
                 "|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["status"] == "error":
                cells = [r["monitorName"], str(r["model"]), r["owner"], f"error: {_cell(r['message'])}", "-", "-", "-"]
            else:
                cells = [r["monitorName"], str(r["model"]), r["owner"],
                         ratio(r["totalItems"], r["totalData"]) if r["totalData"] else "no data",
                         r["topType"] or "-", r["schemaCheck"], r["volume"]]
            lines.append("| " + " | ".join(cells) + " |")  # every cell is already sanitized
        lines += ["", "Anomalous Data / Total Data counts the items of Data Integrity and Data Consistency; the Schema Check "
                  "column counts schema items separately. " + MIXED_NOTE]
        if not_shown:
            lines.append(f"\n{not_shown} more not shown (raise max_monitors, up to 50).")
        return {"status": status, "message": msg, "opening": opening, "window": info,
                "scope": scope, "monitors": rows, "monitorsTotal": total, "notShown": not_shown,
                "verbatim_markdown": "\n".join(lines)}
    except Exception as ex:
        logger.error(f"Error in aw_get_data_quality_overview: {ex}")
        return {"status": "error", "message": f"Error in aw_get_data_quality_overview: {ex}"}

