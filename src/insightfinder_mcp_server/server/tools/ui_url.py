"""Shared builder for InsightFinder UI citation links.

The accessible root-cause/anomaly/deployment view is `/ui/global/systemrootcause`, a
system + day view scoped to a zone. Every event-type tool (incident, log/metric anomaly,
change/deployment, trace) links here — the URL depends only on system_id + zone + day,
not the event type (verified: metric/log/change URLs are identical). `eventPatternType`
is an optional focus filter (used for incidents).
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Optional

logger = logging.getLogger(__name__)


async def build_systemrootcause_url(client, record: dict,
                                    event_pattern_type: Optional[str] = None) -> Optional[str]:
    """Build the `/ui/global/systemrootcause` day-view URL from an event record.

    `record` must provide `projectName` (→ system_id via get_customer_name_for_project)
    and `timestamp` (→ day); `zoneName` selects the zone (falls back to the system default
    zone `zone_<systemId>`), and `userName` the customerName (falls back to the client user).
    Returns None if system_id can't be resolved.
    """
    try:
        base = (getattr(client, "base_url", "") or "").rstrip("/")
        ts = record.get("timestamp")
        project = record.get("projectName")
        if not base or ts is None or not project:
            return None
        system_id = ""
        try:
            info = await client.get_customer_name_for_project(project)
            if info and len(info) > 4:
                system_id = info[4] or ""
        except Exception as e:
            logger.warning(f"systemrootcause url: system id lookup failed: {e}")
        if not system_id:
            return None
        day = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        zone = record.get("zoneName")
        if not zone or zone == "Unknown":  # no named zone → the system default zone
            zone = f"zone_{system_id}"
        # customerName is intentionally omitted — this view resolves without it, and the
        # owner isn't reliably available per record (avoids sending the requester by mistake).
        params = [
            ("redirect", "true"),
            ("e", "All"),
            ("startTime", day),
            ("endTime", day),
            ("s", system_id),
            ("view", "day"),
            ("zoneQuery", zone),
            ("selectedDay", day),
        ]
        if event_pattern_type:
            params.append(("eventPatternType", event_pattern_type))
        return f"{base}/ui/global/systemrootcause?" + "&".join(
            f"{k}={quote(str(v), safe='')}" for k, v in params)
    except Exception as e:
        logger.warning(f"Failed to build systemrootcause url: {e}")
        return None
