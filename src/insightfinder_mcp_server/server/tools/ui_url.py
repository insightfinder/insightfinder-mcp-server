"""Shared builder for InsightFinder UI citation links.

The accessible event view is `/ui/global/systemrootcause`, a system + day view whose
`eventCategory` selects the tab and whose `event*` params locate a specific record.
Scheme (per Pinlong, the UI owner):

    s=<systemId> e=All redirect=true startTime=<day> endTime=<day> zoneQuery=<zone>
    eventCategory=<incident|metric|log|trace|deployment|rootcause>
    eventPatternId=<patternId> eventTimestamp=<startTimestamp ms>
    eventProjectName=<projectName> eventInstanceName=<instance> eventComponentName=<component>

(`customerName` is an optional data-owner param and is intentionally omitted here.)

`eventCategory` is what makes incident vs metric vs log land on the correct tab; the
`event*` params (patternId + timestamp required) pinpoint the specific event. `startTime`
and `endTime` must both be the single day the event occurred.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Optional

logger = logging.getLogger(__name__)

# The tabs the systemrootcause view can land on (eventCategory values).
VALID_EVENT_CATEGORIES = {"incident", "metric", "log", "trace", "deployment", "rootcause"}


# Sub-dicts the list tools nest fields under (metric/deployment → location/metric,
# consolidated → primary_incident). Searched after the record's own top level.
_NESTED_KEYS = ("location", "metric", "deployment", "primary_incident")


def _get(record: dict, *keys):
    """First present, meaningful value among `keys`, searching the record top level then
    its known nested sub-dicts (skips None/""/"Unknown"). List tools flatten the uniform
    timeline record into compact/nested shapes, so field names + nesting vary by tool.
    """
    sources = [record]
    for sub in _NESTED_KEYS:
        v = record.get(sub)
        if isinstance(v, dict):
            sources.append(v)
    for src in sources:
        for k in keys:
            val = src.get(k)
            if val not in (None, "", "Unknown"):
                return val
    return None


async def build_systemrootcause_url(client, record: dict,
                                    event_category: Optional[str] = None,
                                    locate: bool = True,
                                    start_day: Optional[str] = None,
                                    end_day: Optional[str] = None) -> Optional[str]:
    """Build the `/ui/global/systemrootcause` URL for an event record.

    `event_category` (incident|metric|log|trace|deployment|rootcause) selects the tab.
    `locate` adds the per-record `eventPatternId`/`eventTimestamp`/`eventProjectName`/
    `eventInstanceName`/`eventComponentName` params that pinpoint a specific event — emitted
    only when a `patternId` is present (Pinlong: patternId + timestamp are required to
    locate). List tools that drop patternId therefore get a clean tab-level URL.

    `start_day`/`end_day` (``YYYY-MM-DD``) override the day range — pass the calling tool's
    `start_time`/`end_time` args here so a multi-day LIST reflects the queried window rather
    than one arbitrary record's day. When omitted, the range is the record's own day.

    Only a project name (raw OR display) and a `timestamp` are required in `record`; the
    system id, the *raw* projectName, and the owner userName are resolved via
    `get_customer_name_for_project` (which accepts either name and returns all three), so
    records that only kept the display name still produce a correct link. Returns None if
    the system id can't be resolved.
    """
    try:
        base = (getattr(client, "base_url", "") or "").rstrip("/")
        ts = _get(record, "startTimestamp", "timestamp")
        project = _get(record, "projectName", "project", "realProjectName", "project_name")
        if not base or ts is None or not project:
            return None

        # One lookup resolves system_id + the raw projectName, even when `project` is a
        # display name (list tools drop the raw projectName). customerName is intentionally
        # omitted — it's optional and the view resolves without it.
        system_id, raw_project = "", project
        try:
            info = await client.get_customer_name_for_project(project)
            if info and len(info) > 4:
                system_id = info[4] or ""
                raw_project = info[1] or project   # actual_project_name
        except Exception as e:
            logger.warning(f"systemrootcause url: system id lookup failed: {e}")
        if not system_id:
            return None

        day = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        start = start_day or day
        end = end_day or day
        zone = _get(record, "zoneName", "zone", "zone_name")
        if not zone:  # no named zone → the system default zone
            zone = f"zone_{system_id}"

        params = [
            ("e", "All"),
            ("s", system_id),
            ("redirect", "true"),
            ("startTime", start),
            ("endTime", end),
            ("zoneQuery", zone),
        ]
        if event_category and event_category in VALID_EVENT_CATEGORIES:
            params.append(("eventCategory", event_category))

        pattern_id = _get(record, "patternId", "pattern_id", "nid")
        if locate and pattern_id is not None:
            params.append(("eventPatternId", pattern_id))
            params.append(("eventTimestamp", int(ts)))       # startTimestamp in ms
            params.append(("eventProjectName", raw_project))
            instance = _get(record, "instanceName", "instance", "instance_name")
            if instance:
                params.append(("eventInstanceName", instance))
            component = _get(record, "componentName", "component", "component_name")
            if component:
                params.append(("eventComponentName", component))

        return f"{base}/ui/global/systemrootcause?" + "&".join(
            f"{k}={quote(str(v), safe='')}" for k, v in params)
    except Exception as e:
        logger.warning(f"Failed to build systemrootcause url: {e}")
        return None
