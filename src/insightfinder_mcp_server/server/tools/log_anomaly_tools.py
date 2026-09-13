import sys
import json
import re
from typing import Dict, Any, Optional, List, Union, Union
from datetime import datetime, timezone

from ..server import mcp_server
from ..progress import report_progress
from ...api_client.client_factory import get_current_api_client
from ...config.settings import settings
from .get_time import (
    get_time_range_ms,
    resolve_system_timezone,
    resolve_system_identity,
    format_timestamp_in_user_timezone,
    format_api_timestamp_corrected,
    parse_time_parameters,
)

def _get_api_client():
    """
    Get the API client for the current request context.
    
    Returns:
        InsightFinderAPIClient: The API client configured for the current request
        
    Raises:
        ValueError: If no API client is available (missing headers or not in HTTP context)
    """
    api_client = get_current_api_client()
    if not api_client:
        raise ValueError(
            "InsightFinder API client not available. "
            "This tool requires InsightFinder credentials in HTTP headers: "
            "X-InsightFinder-License-Key and X-InsightFinder-User-Name"
        )
    return api_client

def _matches_instance_name(api_instance_name: str, provided_instance_name: str) -> bool:
    """
    Check if the provided instance name matches the API instance name.
    
    Handles both exact matches and partial matches after underscore:
    - "insightfinder-generallogworker-0" matches "insightfinder-generallogworker-0"
    - "insightfinder-generallogworker-0" matches "generallogworker-app_insightfinder-generallogworker-0" (matches part after _)
    
    Args:
        api_instance_name: The instance name returned by the API (e.g., "generallogworker-app_insightfinder-generallogworker-0")
        provided_instance_name: The instance name provided by the user (e.g., "insightfinder-generallogworker-0")
        
    Returns:
        bool: True if the names match (either exactly or after underscore)
    """
    api_name_lower = api_instance_name.lower()
    provided_name_lower = provided_instance_name.lower()
    
    # Case 1: Exact match
    if api_name_lower == provided_name_lower:
        return True
    
    # Case 2: Match the part after underscore
    if "_" in api_name_lower:
        # Extract the part after the last underscore
        part_after_underscore = api_name_lower.split("_")[-1]
        if part_after_underscore == provided_name_lower:
            return True
    
    # Case 3: Check if provided name is in the full API name (loose matching)
    # This handles cases like user providing "generallogworker" should match "generallogworker-app_..."
    if provided_name_lower in api_name_lower:
        return True
    
    return False

# ── Pattern summary ────────────────────────────────────────────────────────────
#
# Log pattern ids are assigned PER INSTANCE by InsightFinder, so they never identify a pattern
# across a project. The pattern *name* does, but only when a user assigned one: unnamed patterns
# resolve to a generic knowledge-base category ("Error", "Fail", ...) or to the numeric id.
# The summary therefore splits anomalies into user-named patterns (one row each) and an "other"
# bucket that is described, not enumerated.

_KB_GENERIC_NAMES = {
    "error", "fail", "failed", "failure", "critical", "exception", "exception:", "warning",
    "warn", "unknown", "info", "other", "default", "none", "null", "n/a",
}
_MNEMONIC_RE = re.compile(r"%[A-Z][A-Z0-9_]*-\d-[A-Z0-9_]+")
_PROC_RE = re.compile(r"\b([A-Za-z][\w.\-]*(?:\[\d+\])?):\s")
_FAMILY_KEYWORDS = (
    ("RSN ERROR", "kernel: RSN ERROR (data frame received with no keys)"),
    ("DOT11_DRV", "kernel: DOT11_DRV client authorize/config failures"),
    ("cleanaird_ipc", "syslog: IPC request error (cleanaird)"),
    ("IPC request", "syslog: IPC request error"),
    (".service", "kernel: systemd service failed"),
    ("CLSM[", "kernel: CLSM client rate-limit failure"),
    ("sshd", "sshd authentication/connection errors"),
    ("Command send fail", "kernel: Command send fail (off-channel IOCTL timeout)"),
    ("offchannel IOCTL", "kernel: Command send fail (off-channel IOCTL timeout)"),
)
_MASKS = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b(?:[0-9a-f]{2}[:.-]){5}[0-9a-f]{2}\b|\b[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}\b", re.I), "<mac>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)?"), "<ts>"),
    (re.compile(r"\[\*?\d\d/\d\d/\d{4} [\d:.]+\]"), ""),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
)

_MODEL_TOP_NAMED = 3


def _summary_for_model(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Trim the summary the model reads: keep totals and the top few named patterns, drop the
    full row list and the rendered block (both are appended to the answer verbatim)."""
    if not summary:
        return summary
    rows = summary.get("named_patterns") or []
    out = {k: v for k, v in summary.items() if k not in ("named_patterns", "verbatim_markdown", "other")}
    out["named_pattern_count"] = len(rows)
    out["top_named_patterns"] = [
        {k: r.get(k) for k in ("pattern_name", "anomalies", "instances", "first_seen", "last_seen")}
        for r in rows[:_MODEL_TOP_NAMED]]
    other = summary.get("other")
    if other:
        # Spell the semantics out in the field names: these groups are UNNAMED anomalies
        # described by their message family, and the outlier share is of raw log lines, not
        # of anomalies. Earlier digests were misread on both points.
        outlier = other.get("volume_outlier")
        out["unnamed_anomalies"] = {
            "anomalies": other.get("anomalies"),
            "instances": other.get("instances"),
            "insightfinder_categories": other.get("categories"),
            "largest_unnamed_groups_by_message": [
                {"message_group_not_a_pattern_name": f.get("family"),
                 "anomalies": f.get("anomalies"), "instances": f.get("instances")}
                for f in (other.get("top_families") or [])],
            "smaller_unnamed_groups": max((other.get("total_families") or 0)
                                          - len(other.get("top_families") or []), 0),
            "log_volume_outlier": ({
                "instance": outlier.get("instance"),
                "percent_of_ALL_raw_log_lines_in_range_from_this_instance":
                    round(outlier.get("share_of_log_lines", 0) * 100),
                "its_anomaly_count": outlier.get("anomalies"),
                "its_message_group": outlier.get("family"),
            } if outlier else None),
        }
    out["how_to_read"] = (
        "top_named_patterns are user-assigned InsightFinder pattern names. Everything under "
        "unnamed_anomalies has NO pattern name; its groups are message families and must not be "
        "presented as named patterns or merged with a similarly worded named pattern. The log "
        "volume outlier is a share of raw log lines in the range, not a share of anomalies.")
    return out


# Layout thresholds for the named-pattern table.
_NAMED_FULL_MAX = 12       # <= this many named patterns: full table
_NAMED_COMPACT_MAX = 40    # <= this many: top-5 table + inline list; above: top-15 + "and N more"
_OTHER_FAMILIES_MAX = 5    # families listed for the unnamed bucket
_VOLUME_OUTLIER_SHARE = 0.20  # one instance above this share of log lines gets a bullet


def pattern_name_kind(pattern_name: Any) -> str:
    """'named' (user-assigned, meaningful), 'generic' (KB category), 'numeric' (bare id), 'missing'."""
    if pattern_name is None:
        return "missing"
    name = str(pattern_name).strip()
    if not name:
        return "missing"
    if name.replace(".", "", 1).isdigit():
        return "numeric"
    if name.lower() in _KB_GENERIC_NAMES or len(name) <= 2:
        return "generic"
    return "named"


def _count(anomaly: Dict[str, Any]) -> int:
    try:
        return max(int(anomaly.get("count") or 1), 1)
    except (TypeError, ValueError):
        return 1


def _instance(anomaly: Dict[str, Any]) -> str:
    return (anomaly.get("instanceName") or anomaly.get("realInstanceName")
            or anomaly.get("anomalyLogInstance") or anomaly.get("projectInstanceName") or "")


def _ts(anomaly: Dict[str, Any]) -> int:
    return int(anomaly.get("timestamp") or anomaly.get("startTimestamp") or 0)


def _hhmm(ts_ms: int) -> str:
    # InsightFinder timestamps are owner-timezone wall clock encoded as UTC epoch.
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M") if ts_ms else ""


def _raw_text(anomaly: Dict[str, Any]) -> str:
    raw = anomaly.get("rawData")
    if raw is None:
        return ""
    return " ".join((raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)).split())


def message_family(raw: str) -> str:
    """Coarse label for an unnamed anomaly: syslog mnemonic, a known keyword family, or a masked
    template of the first tokens after the emitting process."""
    m = _MNEMONIC_RE.search(raw)
    if m:
        return m.group(0)
    for needle, label in _FAMILY_KEYWORDS:
        if needle in raw:
            return label
    s = raw
    procs = [pm for pm in _PROC_RE.finditer(s)
             if pm.group(1).lower() not in ("utc", "pdt", "edt", "pst", "est", "cst", "cdt", "mst", "mdt")]
    if procs:
        s = s[procs[0].start():]
    for rx, rep in _MASKS:
        s = rx.sub(rep, s)
    toks = s.split()
    return " ".join(toks[:8]) if toks else "(no message)"


def _md_cell(v: Any) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def build_pattern_summary(anomalies: List[Dict[str, Any]], tz_name: str, system_name: str,
                          project_name: str, date_label: str) -> Dict[str, Any]:
    """
    Summarize a project's log anomalies for one query.

    User-named patterns get one row each (anomaly count, instances, first/last seen). Everything
    without a user-assigned name is described as one bucket: total, KB category split, the largest
    message families, and a volume outlier when one instance produced most of the log lines.

    Returns a dict with structured fields and `verbatim_markdown`, a ready-to-append markdown
    block (header, named-pattern table sized by layout thresholds, "other" paragraph).
    """
    total = len(anomalies)
    if total == 0:
        return {"total_anomalies": 0, "named_patterns": [], "other": None, "layout": "empty",
                "verbatim_markdown": ""}
    instances_all = {_instance(a) for a in anomalies if _instance(a)}
    ts_all = [_ts(a) for a in anomalies if _ts(a)]
    window = {"start": _hhmm(min(ts_all)) if ts_all else "", "end": _hhmm(max(ts_all)) if ts_all else "",
              "timezone": tz_name}

    named: Dict[str, List[Dict[str, Any]]] = {}
    other: List[Dict[str, Any]] = []
    for a in anomalies:
        name = a.get("patternName")
        if pattern_name_kind(name) == "named":
            named.setdefault(str(name).strip(), []).append(a)
        else:
            other.append(a)

    named_rows = []
    for name, group in named.items():
        insts = sorted({_instance(a) for a in group if _instance(a)})
        tss = [_ts(a) for a in group if _ts(a)]
        named_rows.append({
            "pattern_name": name,
            "anomalies": len(group),
            "instances": len(insts),
            "sample_instances": insts[:3],
            "first_seen": _hhmm(min(tss)) if tss else "",
            "last_seen": _hhmm(max(tss)) if tss else "",
            "sample_raw": _raw_text(group[0])[:240],
        })
    named_rows.sort(key=lambda r: (-r["anomalies"], -r["instances"], r["pattern_name"].lower()))

    other_summary = None
    if other:
        cats: Dict[str, int] = {}
        for a in other:
            kind = pattern_name_kind(a.get("patternName"))
            key = str(a.get("patternName")).strip() if kind == "generic" else (
                "numeric id only" if kind == "numeric" else "no name")
            cats[key] = cats.get(key, 0) + 1
        fam_count: Dict[str, int] = {}
        fam_inst: Dict[str, set] = {}
        for a in other:
            f = message_family(_raw_text(a))
            fam_count[f] = fam_count.get(f, 0) + 1
            fam_inst.setdefault(f, set()).add(_instance(a))
        families = sorted(({"family": f, "anomalies": n, "instances": len(fam_inst[f])}
                           for f, n in fam_count.items()),
                          key=lambda r: (-r["anomalies"], r["family"]))
        total_lines = sum(_count(a) for a in anomalies)
        by_inst: Dict[str, int] = {}
        for a in other:
            by_inst[_instance(a)] = by_inst.get(_instance(a), 0) + _count(a)
        outlier = None
        if total_lines and by_inst:
            top_inst, top_lines = max(by_inst.items(), key=lambda kv: kv[1])
            share = top_lines / total_lines
            if share >= _VOLUME_OUTLIER_SHARE:
                recs = [a for a in other if _instance(a) == top_inst]
                recs.sort(key=_count, reverse=True)
                outlier = {"instance": top_inst, "share_of_log_lines": round(share, 3),
                           "anomalies": len(recs), "family": message_family(_raw_text(recs[0]))}
        other_summary = {
            "anomalies": len(other),
            "instances": len({_instance(a) for a in other if _instance(a)}),
            "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "top_families": families[:_OTHER_FAMILIES_MAX],
            "total_families": len(families),
            "volume_outlier": outlier,
        }

    n_named = len(named_rows)
    layout = "full" if n_named <= _NAMED_FULL_MAX else ("compact" if n_named <= _NAMED_COMPACT_MAX else "grouped")

    # ── markdown ──
    lines = [f"### Log anomalies: {system_name} / {project_name}, {date_label}", ""]
    lines.append(f"**{total:,} anomalies** across **{len(instances_all):,} instances**, "
                 f"{window['start']} to {window['end']} {tz_name}. All anomalies in the time range were retrieved.")
    lines.append("")
    if named_rows:
        named_total = sum(r["anomalies"] for r in named_rows)
        table_rows = named_rows if layout == "full" else (named_rows[:5] if layout == "compact" else named_rows[:15])
        head = f"**Named patterns** ({n_named} patterns, {named_total:,} anomalies)"
        if layout != "full":
            head += f". Top {len(table_rows)} by anomaly count:"
        lines.append(head)
        lines.append("")
        lines.append("| Pattern | Anomalies | Instances | Window |")
        lines.append("|---|---|---|---|")
        for r in table_rows:
            win = r["first_seen"] if r["first_seen"] == r["last_seen"] else f"{r['first_seen']} to {r['last_seen']}"
            lines.append(f"| {_md_cell(r['pattern_name'])} | {r['anomalies']:,} | {r['instances']:,} | {win} |")
        rest = named_rows[len(table_rows):]
        if rest and layout == "compact":
            lines.append("")
            lines.append("Also seen: " + ", ".join(f"{_md_cell(r['pattern_name'])} ({r['anomalies']})" for r in rest) + ".")
        elif rest:
            lines.append("")
            lines.append(f"And {len(rest)} more named patterns ({sum(r['anomalies'] for r in rest):,} anomalies). "
                         f"Ask for the full list or for any pattern by name.")
    else:
        lines.append("No user-named patterns in this range.")
    lines.append("")
    if other_summary:
        o = other_summary
        cat_txt = ", ".join(f"{k} ({v:,})" for k, v in o["categories"].items())
        lines.append(f"**Other anomalies without an assigned pattern name:** {o['anomalies']:,} anomalies on "
                     f"{o['instances']:,} instances. InsightFinder category: {cat_txt}.")
        if o["volume_outlier"]:
            v = o["volume_outlier"]
            lines.append(f"- {int(v['share_of_log_lines']*100)}% of all log lines in this range come from one instance, "
                         f"{v['instance']}: {v['family']} ({v['anomalies']} anomalies).")
        if o["top_families"]:
            fam_txt = "; ".join(f"{f['family']} on {f['instances']:,} instances ({f['anomalies']:,})"
                                for f in o["top_families"])
            more = o["total_families"] - len(o["top_families"])
            lines.append(f"- Largest groups by anomaly count: {fam_txt}" + (f"; and {more} smaller groups." if more > 0 else "."))
        lines.append("")
    lines.append("Ask for any pattern by name to see its individual anomalies and raw log lines.")

    return {
        "total_anomalies": total,
        "total_instances": len(instances_all),
        "window": window,
        "layout": layout,
        "named_patterns": named_rows,
        "other": other_summary,
        "verbatim_markdown": "\n".join(lines),
    }


# Layer 0: Ultra-compact log anomaly overview (just counts and basic info)
@mcp_server.tool()
async def get_log_anomalies_overview(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches a very high-level overview of log anomalies - just counts and basic metrics.
    This is the most compact view, ideal for initial exploration.
    Use this tool when a user first asks about log anomalies to get a quick overview.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 17", "March 5") without a year, always default to year 2026.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time (Optional[Union[str, int]]): The start of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
            If not provided, defaults to 24 hours ago.
        end_time (Optional[Union[str, int]]): The end of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
            If not provided, defaults to the current time.
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            # log_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]
        
        # Basic counts and metrics
        total_anomalies = len(log_anomalies)
        
        # Time range analysis
        if log_anomalies:
            timestamps = [anomaly["timestamp"] for anomaly in log_anomalies]
            first_anomaly = min(timestamps)
            last_anomaly = max(timestamps)
        else:
            first_anomaly = last_anomaly = None

        # Component and instance analysis (just unique counts)
        unique_components = len(set(anomaly.get("componentName", "Unknown") for anomaly in log_anomalies))
        unique_instances = len(set(anomaly.get("instanceName", "Unknown") for anomaly in log_anomalies))
        unique_patterns = len(set(anomaly.get("patternName", "Unknown") for anomaly in log_anomalies))
        unique_projects = len(set(anomaly.get("projectDisplayName", "Unknown") for anomaly in log_anomalies))
        unique_zones = len(set(anomaly.get("zoneName", "Unknown") for anomaly in log_anomalies if anomaly.get("zoneName")))

        # Anomaly score statistics
        if log_anomalies:
            scores = [anomaly.get("anomalyScore", 0) for anomaly in log_anomalies]
            max_score = max(scores)
            min_score = min(scores)
            avg_score = sum(scores) / len(scores)
        else:
            max_score = min_score = avg_score = 0

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "summary": {
                "total_anomalies": total_anomalies,
                "unique_components": unique_components,
                "unique_instances": unique_instances,
                "unique_patterns": unique_patterns,
                "unique_projects": unique_projects,
                "unique_zones": unique_zones,
                "score_statistics": {
                    "max_score": round(max_score, 2),
                    "min_score": round(min_score, 2),
                    "avg_score": round(avg_score, 2)
                },
                "first_anomaly": format_api_timestamp_corrected(first_anomaly, tz_name) if first_anomaly else None,
                "last_anomaly": format_api_timestamp_corrected(last_anomaly, tz_name) if last_anomaly else None,
                "has_anomalies": total_anomalies > 0
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_overview: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 1: Enhanced log anomaly list with detailed information
@mcp_server.tool()
async def get_log_anomalies_list(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 10,
    project_name: Optional[str] = None,
    include_raw_data: bool = False
) -> Dict[str, Any]:
    """
    Fetches a detailed list of log anomalies with comprehensive information.
    This is the main tool for getting log anomaly details - combines basic info with parsed raw data.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 17", "March 5") without a year, always default to year 2026.

    The response includes parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.) and formatted summaries.

    Args:
        system_name (str): The name of the system to query for log anomalies.
        start_time (Optional[Union[str, int]]): The start of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): The end of the time window.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit (int): Maximum number of anomalies to return (default: 10).
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
        include_raw_data (bool): Whether to include raw log data (default: False for performance).
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Call the InsightFinder API client
        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            # log_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]

        # Sort by anomaly score (highest first) and limit
        log_anomalies = sorted(log_anomalies, key=lambda x: x.get("anomalyScore", 0), reverse=True)[:limit]

        # Create detailed anomaly list
        anomaly_list = []
        for i, anomaly in enumerate(log_anomalies):                
            anomaly_info = {
                "id": i + 1,
                "timestamp": anomaly["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(anomaly["timestamp"], tz_name),
                "project": anomaly.get("projectDisplayName", "Unknown"),
                "component": anomaly.get("componentName", "Unknown"),
                "instance": anomaly.get("instanceName", "Unknown"),
                "pattern": anomaly.get("patternName", "Unknown"),
                "zone": anomaly.get("zoneName", "Unknown"),
                "anomaly_score": round(anomaly.get("anomalyScore", 0), 2),
                "is_incident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0)
            }
            
            # Add raw data if requested and available
            if "rawData" in anomaly and anomaly["rawData"]:
                raw_data = anomaly["rawData"]
                
                # Parse and format raw data for better display
                parsed_data = None
                raw_data_fields = {}
                
                # Try to parse JSON if it's a string
                if isinstance(raw_data, str):
                    try:
                        parsed_data = json.loads(raw_data)
                        raw_data_fields = parsed_data if isinstance(parsed_data, dict) else {}
                        anomaly_info["raw_data_type"] = "json_string"
                    except json.JSONDecodeError:
                        # Not JSON, treat as plain text
                        raw_data_fields = {"content": raw_data}
                        anomaly_info["raw_data_type"] = "plain_text"
                elif isinstance(raw_data, dict):
                    # Already a dictionary
                    raw_data_fields = raw_data
                    parsed_data = raw_data
                    anomaly_info["raw_data_type"] = "dictionary"
                else:
                    # Other data types
                    raw_data_fields = {"content": str(raw_data)}
                    anomaly_info["raw_data_type"] = "other"
                
                # Add parsed fields for easy access
                if raw_data_fields:
                    anomaly_info["raw_data_fields"] = raw_data_fields
                    
                    # Extract common fields if they exist
                    common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time", "execution_uid"]
                    extracted_fields = {}
                    for field in common_fields:
                        if field in raw_data_fields:
                            print(f"Extracting field '{field}' from raw data: {raw_data_fields[field]}")
                            extracted_fields[field] = raw_data_fields[field]
                    
                    if extracted_fields:
                        anomaly_info["key_fields"] = extracted_fields
                
                # Include full raw data if requested
                if include_raw_data:
                    anomaly_info["raw_data"] = raw_data

                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "system_name": system_name,
            "filters": {
                "limit": limit,
                "project_name": project_name,
                "include_raw_data": include_raw_data
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "total_found": len(result["data"]),
            "returned_count": len(anomaly_list),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_list: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Layer 2: Statistics and analysis tools
@mcp_server.tool()
async def get_log_anomalies_statistics(
    system_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Provides comprehensive statistical analysis of log anomalies for a system over a time period.
    Use this tool to understand anomaly patterns, frequency, distribution, and impact across components.
    Ideal for comparing log anomalies between time periods.

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 17", "March 5") without a year, always default to year 2026.

    ⚠️ RELATIVE DATE KEYWORDS SUPPORTED:
    You can use simple keywords instead of explicit dates:
    - "thisweek" or "this_week": Monday to today
    - "lastweek" or "last_week": Last Monday to Last Sunday
    - "thismonth" or "this_month": 1st of current month to today
    - "lastmonth" or "last_month": 1st of last month to last day of last month
    - "today": Today's date (full day)
    - "yesterday": Yesterday's date (full day)

    COMPARISON EXAMPLES - Use these keywords directly without calculating dates:
        To compare "This week" vs "Last week":
        - Call 1: start_time="thisweek", end_time="thisweek"
        - Call 2: start_time="lastweek", end_time="lastweek"

        To compare "This month" vs "Last month":
        - Call 1: start_time="thismonth", end_time="thismonth"
        - Call 2: start_time="lastmonth", end_time="lastmonth"

    Args:
        system_name (str): The name of the system to analyze.
        start_time (Optional[Union[str, int]]): The start of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        end_time (Optional[Union[str, int]]): The end of the time window.
            - Relative keywords: "thisweek", "lastweek", "thismonth", "lastmonth", "today", "yesterday"
            - Absolute dates: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds
        project_name (str): Optional. Filter results to only include anomalies from this specific project.
    
    Returns:
        Statistical breakdown with anomaly counts, score analysis, and top affected components, instances, and projects.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Parse time parameters (supports both keywords and absolute dates)
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms  # 24 hours ago

        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        api_client = _get_api_client()
        result = await api_client.get_loganomaly(
            system_name=system_name,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        if result["status"] != "success":
            return result

        log_anomalies = result["data"]
        
        # Filter by project name if specified
        if project_name:
            log_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # Filter by anomaly type if specified (e.g. "whiteList")
        # if anomaly_type:
        #     log_anomalies = [la for la in log_anomalies if str(la.get("type", "")).lower() == anomaly_type.lower()]

        # Calculate statistics
        total_anomalies = len(log_anomalies)
        
        # Group by component, instance, pattern, zone, and project
        components = {}
        instances = {}
        patterns = {}
        zones = {}
        projects = {}
        
        for anomaly in log_anomalies:
            # Component analysis
            component = anomaly.get("componentName", "Unknown")
            components[component] = components.get(component, 0) + 1
            
            # Instance analysis
            instance = anomaly.get("instanceName", "Unknown")
            instances[instance] = instances.get(instance, 0) + 1
            
            # Pattern analysis
            pattern = anomaly.get("patternName", "Unknown")
            patterns[pattern] = patterns.get(pattern, 0) + 1
            
            # Zone analysis
            zone = anomaly.get("zoneName", "Unknown")
            if zone != "Unknown":
                zones[zone] = zones.get(zone, 0) + 1
                
            # Project analysis
            project = anomaly.get("projectDisplayName", "Unknown")
            projects[project] = projects.get(project, 0) + 1

        # Score statistics
        if log_anomalies:
            scores = [anomaly.get("anomalyScore", 0) for anomaly in log_anomalies]
            max_score = max(scores)
            min_score = min(scores)
            avg_score = sum(scores) / len(scores)
        else:
            max_score = min_score = avg_score = 0

        return {
            "status": "success",
            "system_name": system_name,
            "timezone": tz_name,
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "statistics": {
                "total_anomalies": total_anomalies,
                "score_statistics": {
                    "max_score": round(max_score, 2),
                    "min_score": round(min_score, 2),
                    "avg_score": round(avg_score, 2)
                },
                "top_affected_components": dict(sorted(components.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_instances": dict(sorted(instances.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_patterns": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_affected_projects": dict(sorted(projects.items(), key=lambda x: x[1], reverse=True)[:10]),
                "top_zones": dict(sorted(zones.items(), key=lambda x: x[1], reverse=True)[:10]) if zones else {}
            }
        }
        
    except Exception as e:
        error_message = f"Error in get_log_anomalies_statistics: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}

# Project-specific query tool
@mcp_server.tool()
async def get_project_log_anomalies(
    system_name: str,
    project_name: str,
    start_time: Optional[Union[str, int]] = None,
    end_time: Optional[Union[str, int]] = None,
    limit: int = 20,
    offset: int = 0,
    instance_name: Optional[str] = None,
    include_raw_data: bool = True,
    pattern_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches log anomalies specifically for a given project within a system with pagination support.
    This function includes detailed raw data and comprehensive information for each anomaly.
    Use this tool when the user specifies both a system name and project name.

    COVERAGE: the tool retrieves EVERY log anomaly of the project in the time range (paged
    server-side, no cap) and returns `pattern_summary` computed over all of them, plus
    `verbatim_markdown`, a finished summary block (header, table of user-named patterns, one
    paragraph for anomalies without an assigned name).

    HOW TO ANSWER (important):
    - The complete summary (header, table of all named patterns, "other" paragraph) is appended
      to your final answer automatically by the platform; you only see a digest of it. Do NOT
      write a table or a list of patterns. Write 2-4 sentences of interpretation (what stands
      out, likely relationships between patterns, what to check), then stop.
    - Pattern ids are per instance and do not identify a pattern across the project; never
      report pattern ids. Use pattern names.
    - When the user asks about ONE pattern by name (e.g. "show me the BEANSTALK_JOB_MAX_RETRY_EXCEEDED
      anomalies"), call this tool with `pattern_name` set; then `anomalies` holds that pattern's
      individual anomalies with raw log lines, and the summary block is skipped.
    - `anomalies` is only the detail page selected by limit/offset (newest first).

    ⚠️ YEAR DEFAULT: If the user provides only a month and day (e.g., "May 17", "March 5") without a year, always default to year 2026.
    
    The response includes full anomaly details with:
    - Basic anomaly information (timestamp, component, instance, pattern, zone, score)
    - Parsed raw data fields (e.g., _id, cdn, status_code, url, name, etc.)
    - Key fields extraction for common data elements (including 'cdn' if available)
    - Formatted summaries for better readability
    - JSON parsing for structured data display
    
    Note for LLM: When presenting anomaly details, always list the 'cdn' field if it is present in the data.
    
    Example usage:
    - "show me log anomalies for project demo-kpi-metrics-2 in system InsightFinder Demo System (APP)"
    - "get log anomalies before incident for project X in system Y"
    - "show me next 20 anomalies" (use offset parameter)
    - "show me anomalies for instance instance-1" (use instance_name parameter)

    Args:
        system_name (str): The system the USER named (e.g., "NBC Distribution POC"). Pass it
            exactly as the user said it. Never derive a system from the project name: a
            project such as "splunk-historical" can exist in several systems, and searching
            systems for "splunk" will pick the wrong one. If the user did not name a system,
            call list_all_systems_and_projects and choose the system that contains the
            project (ask the user if more than one does).
        project_name (str): The name of the project (e.g., "demo-kpi-metrics-2")
        start_time (Optional[Union[str, int]]): Start time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        end_time (Optional[Union[str, int]]): End time.
            Accepts: "2026-02-12T11:05:00", "2026-02-12", "02/12/2026", or milliseconds.
        limit (int): Maximum number of anomalies to return (default: 20)
        offset (int): Number of anomalies to skip for pagination (default: 0)
        instance_name (str): Optional. Filter results by specific instance name.
        include_raw_data (bool): Whether to include full raw data details (default: True)
        pattern_name (str): Optional. Restrict to one user-named pattern (case-insensitive;
            exact name preferred, substring accepted). Skips the summary block.
    """
    try:
        # Resolve owner timezone for this system
        tz_name, system_name = await resolve_system_timezone(system_name)

        # Convert timestamps
        try:
            start_time_ms, end_time_ms = parse_time_parameters(start_time, end_time, tz_name)
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Set default time range if not provided (timezone-aware)
        if end_time_ms is None or start_time_ms is None:
            default_start_ms, default_end_ms = get_time_range_ms(tz_name, 1)
            if end_time_ms is None:
                end_time_ms = default_end_ms
            if start_time_ms is None:
                start_time_ms = default_start_ms
        
        # Expand if start/end are equal (day expansion)
        if start_time_ms is not None and end_time_ms is not None and start_time_ms == end_time_ms:
            dt = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999000)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)

        # Validate timestamps
        current_time_ms = int(datetime.now().timestamp() * 1000)
        two_days_ms = 2 * 24 * 60 * 60 * 1000
        
        # Check for future timestamps > 2 days
        if start_time_ms > current_time_ms + two_days_ms or end_time_ms > current_time_ms + two_days_ms:
            return {
                "status": "error",
                "message": "Timestamps cannot be more than 2 days in the future."
            }

        print(f"Fetching loganomaly data for {system_name}...", file=sys.stderr)

        # Validate timestamps
        current_time_ms = int(datetime.now().timestamp() * 1000)
        two_days_ms = 2 * 24 * 60 * 60 * 1000
        
        # Check for future timestamps > 2 days
        if start_time_ms > current_time_ms + two_days_ms or end_time_ms > current_time_ms + two_days_ms:
            return {
                "status": "error",
                "message": "Timestamps cannot be more than 2 days in the future."
            }
            
        # Handle same start/end time - expand to full day
        if start_time_ms == end_time_ms:
            # Create a datetime object from the timestamp (assuming UTC)
            dt = datetime.fromtimestamp(start_time_ms / 1000)
            
            # Set to beginning of day (00:00:00)
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            start_time_ms = int(start_dt.timestamp() * 1000)
            
            # Set to end of day (23:59:59)
            end_dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            end_time_ms = int(end_dt.timestamp() * 1000)

        print(f"Fetching loganomaly data for {system_name}...", file=sys.stderr)

        api_client = _get_api_client()

        # Preferred path: the paged external timeline API, which returns every record for the
        # range with no client-side record cap. Needs the system id and owner.
        report_progress(f"Resolving system {system_name}", stage="resolve")
        identity = await resolve_system_identity(system_name)
        result = None
        data_source = "paged"
        if identity.get("system_id") and identity.get("owner"):
            result = await api_client.get_loganomaly_all(
                customer_name=identity["owner"],
                system_id=identity["system_id"],
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if result.get("status") != "success":
                print(f"Paged log anomaly fetch failed for {system_name} "
                      f"({result.get('message', 'unknown error')}); falling back to /api/v2/timeline",
                      file=sys.stderr)
                result = None

        if result is None:
            # Fallback: the unpaged system-wide timeline (capped at 5000 records / 10 MB).
            data_source = "unpaged"
            result = await api_client.get_loganomaly(
                system_name=system_name,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )

        print(f"API call completed for {system_name} via {data_source}. Status: {result.get('status', 'unknown')}", file=sys.stderr)

        if result["status"] != "success":
            error_message = f"API error for {system_name}: {result.get('message', 'Unknown error')}"
            print(error_message, file=sys.stderr)
            return result
        log_anomalies = result["data"]
        print(f"Retrieved {len(log_anomalies)} log anomalies for {system_name}", file=sys.stderr)

        # Filter by the specific project name
        # project_anomalies = [la for la in log_anomalies if la.get("projectName") == project_name]
        project_anomalies = [la for la in log_anomalies if la.get("projectName", "").lower() == project_name.lower() or la.get("projectDisplayName", "").lower() == project_name.lower()]

        # Filter by instance name if provided (with smart matching for different formats)
        if instance_name:
            project_anomalies = [
                la for la in project_anomalies 
                if _matches_instance_name(la.get("instanceName", ""), instance_name)
            ]
            # if settings.ENABLE_DEBUG_MESSAGES:
            #     print(f"[Instance filter] Filtered by instance_name='{instance_name}', remaining: {len(project_anomalies)}", file=sys.stderr)

        # Always only return anomalies of type "whiteList" for project-specific queries
        project_anomalies = [la for la in project_anomalies if str(la.get("type", "")).lower() == "whitelist"]

        report_progress(f"Analyzing {len(project_anomalies):,} log anomalies of {project_name} into patterns",
                        current=len(project_anomalies), total=len(project_anomalies), stage="analyze")
        # Summary over the COMPLETE filtered set (not just the page below), unless drilling into
        # one pattern by name.
        pattern_summary = None
        if pattern_name:
            wanted = pattern_name.strip().lower()
            exact = [la for la in project_anomalies if str(la.get("patternName", "")).strip().lower() == wanted]
            project_anomalies = exact or [la for la in project_anomalies
                                          if wanted in str(la.get("patternName", "")).lower()]
        else:
            date_label = format_timestamp_in_user_timezone(start_time_ms, tz_name)[:10]
            end_label = format_timestamp_in_user_timezone(end_time_ms, tz_name)[:10]
            if end_label != date_label:
                date_label = f"{date_label} to {end_label}"
            pattern_summary = build_pattern_summary(project_anomalies, tz_name, system_name,
                                                    project_name, date_label)

        # Sort by timestamp (most recent first)
        project_anomalies = sorted(project_anomalies, key=lambda x: x.get("timestamp", 0) or 0, reverse=True)
        
        # Calculate total count before pagination
        total_project_anomalies = len(project_anomalies)
        
        # Apply pagination
        paginated_anomalies = project_anomalies[offset : offset + limit]
        has_more = (offset + limit) < total_project_anomalies

        # Create detailed anomaly list for the project
        anomaly_list = []
        for i, anomaly in enumerate(paginated_anomalies):                
            anomaly_info = {
                "id": offset + i + 1,  # Global ID based on offset
                "timestamp": anomaly["timestamp"],
                "timestamp_human": format_api_timestamp_corrected(anomaly["timestamp"], tz_name),
                "project": anomaly.get("projectDisplayName", "Unknown"),
                "component": anomaly.get("componentName", "Unknown"),
                "instance": anomaly.get("instanceName", "Unknown"),
                "pattern": anomaly.get("patternName", "Unknown"),
                "zone": anomaly.get("zoneName", "Unknown"),
                "anomaly_score": round(anomaly.get("anomalyScore", 0), 2),
                "is_incident": anomaly.get("isIncident", False),
                "active": anomaly.get("active", 0)
            }
            
            # Add raw data details if available
            if "rawData" in anomaly and anomaly["rawData"]:
                raw_data = anomaly["rawData"]
                
                # Parse and format raw data for better display
                parsed_data = None
                raw_data_fields = {}
                
                # Try to parse JSON if it's a string
                if isinstance(raw_data, str):
                    try:
                        parsed_data = json.loads(raw_data)
                        raw_data_fields = parsed_data if isinstance(parsed_data, dict) else {}
                        anomaly_info["raw_data_type"] = "json_string"
                    except json.JSONDecodeError:
                        # Not JSON, treat as plain text
                        raw_data_fields = {"content": raw_data}
                        anomaly_info["raw_data_type"] = "plain_text"
                elif isinstance(raw_data, dict):
                    # Already a dictionary
                    raw_data_fields = raw_data
                    parsed_data = raw_data
                    anomaly_info["raw_data_type"] = "dictionary"
                else:
                    # Other data types
                    raw_data_fields = {"content": str(raw_data)}
                    anomaly_info["raw_data_type"] = "other"
                
                # Add parsed fields for easy access
                if raw_data_fields:
                    anomaly_info["raw_data_fields"] = raw_data_fields
                    
                    # Extract common fields if they exist
                    common_fields = ["_id", "cdn", "id", "status_code", "status_text", "url", "name", "product", "location", "time", "execution_uid"]
                    extracted_fields = {}
                    for field in common_fields:
                        if field in raw_data_fields:
                            extracted_fields[field] = raw_data_fields[field]
                    
                    if extracted_fields:
                        anomaly_info["key_fields"] = extracted_fields
                
                # Include full raw data if requested
                if include_raw_data:
                    anomaly_info["raw_data"] = raw_data
                                    
                anomaly_info["has_raw_data"] = True
            else:
                anomaly_info["has_raw_data"] = False
            
            anomaly_list.append(anomaly_info)

        return {
            "status": "success",
            "query_type": "project_specific_log_anomalies",
            "system_name": system_name,
            "project_name": project_name,
            "instance_filter": instance_name,
            "include_raw_data": include_raw_data,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "total_available": total_project_anomalies,
                "returned_count": len(anomaly_list),
                "has_more": has_more
            },
            "time_range": {
                "start_human": format_timestamp_in_user_timezone(start_time_ms, tz_name),
                "end_human": format_timestamp_in_user_timezone(end_time_ms, tz_name)
            },
            "total_system_anomalies": len(log_anomalies),
            "data_source": data_source,
            "coverage": ("complete: every log anomaly in the time range was retrieved"
                         if data_source == "paged" else
                         "may be partial: unpaged API capped at 5000 records"),
            "pattern_filter": pattern_name,
            # Model-facing digest: totals, the top named patterns and the "other" bucket. The
            # complete named-pattern table travels ONLY in `verbatim_markdown`, which the
            # platform appends to the final answer and hides from the model.
            "pattern_summary": _summary_for_model(pattern_summary),
            "verbatim_markdown": (pattern_summary or {}).get("verbatim_markdown", ""),
            "anomalies": anomaly_list
        }
        
    except Exception as e:
        error_message = f"Error in get_project_log_anomalies: {str(e)}"
        if settings.ENABLE_DEBUG_MESSAGES:
            print(error_message, file=sys.stderr)
        return {"status": "error", "message": error_message}
