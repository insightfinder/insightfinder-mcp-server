"""
Timezone-aware time utilities for the InsightFinder MCP server.

Key Design Principle (from timestamp-details.md):
    All timestamps from the InsightFinder backend are in 13-digit millisecond format
    based on the Owner User Timezone. They are NOT converted per logged-in user.
    The Owner User Timezone is the single source of truth.

    CRITICAL: The 13-digit timestamps are NOT real UTC epochs!
    They represent the wall-clock time in the Owner User Timezone, encoded
    as if that wall-clock time were UTC.

    Example: Owner TZ = US/Mountain, event at 7:50 AM Mountain
        -> stored as epoch for 7:50 AM UTC (NOT 1:50 PM UTC)
        -> i.e. the epoch value = calendar.timegm(naive_local_time.timetuple()) * 1000

    Therefore:
        - To GENERATE timestamps for the API: get wall-clock in owner tz,
          strip tzinfo, treat as UTC, get epoch.
        - To DISPLAY timestamps from the API: treat the epoch as UTC,
          read the wall-clock directly, label with tz_name. No conversion.

How timezone is resolved:
    1. Call /api/external/v1/systemframework to get user's systems
    2. Check ownSystemArr first - pick timezone from the first owned system
    3. If ownSystemArr is empty, check shareSystemArr
    4. If neither has data, default to UTC
"""

import calendar
import json
import logging
import re
import zoneinfo
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any, Union

from ..server import mcp_server
from ...api_client.client_factory import get_current_api_client

logger = logging.getLogger(__name__)

_FALLBACK_TZ = "UTC"

# Legacy timezone names that Python's zoneinfo may not recognize without the
# `tzdata` package.  InsightFinder's API frequently returns these.
_LEGACY_TZ_MAP: dict[str, str] = {
    "US/Eastern":    "America/New_York",
    "US/Central":    "America/Chicago",
    "US/Mountain":   "America/Denver",
    "US/Pacific":    "America/Los_Angeles",
    "US/Alaska":     "America/Anchorage",
    "US/Hawaii":     "Pacific/Honolulu",
    "US/Arizona":    "America/Phoenix",
    "US/East-Indiana": "America/Indiana/Indianapolis",
    "US/Michigan":   "America/Detroit",
    "US/Samoa":      "Pacific/Pago_Pago",
    "Canada/Eastern":   "America/Toronto",
    "Canada/Central":   "America/Winnipeg",
    "Canada/Mountain":  "America/Edmonton",
    "Canada/Pacific":   "America/Vancouver",
    "Canada/Atlantic":  "America/Halifax",
    "Canada/Newfoundland": "America/St_Johns",
    "Canada/Saskatchewan": "America/Regina",
    "Japan":          "Asia/Tokyo",
    "Singapore":      "Asia/Singapore",
    "Hongkong":       "Asia/Hong_Kong",
    "ROK":            "Asia/Seoul",
    "ROC":            "Asia/Taipei",
    "PRC":            "Asia/Shanghai",
    "Egypt":          "Africa/Cairo",
    "Turkey":         "Europe/Istanbul",
    "Israel":         "Asia/Jerusalem",
    "Iran":           "Asia/Tehran",
    "GB":             "Europe/London",
    "Portugal":       "Europe/Lisbon",
    "Poland":         "Europe/Warsaw",
    "Cuba":           "America/Havana",
    "Jamaica":        "America/Jamaica",
    "Brazil/East":    "America/Sao_Paulo",
    "Brazil/West":    "America/Manaus",
    "Mexico/General": "America/Mexico_City",
    "NZ":             "Pacific/Auckland",
}


def _normalize_tz(tz_name: str) -> Optional[str]:
    """
    Validate and normalize a timezone name.

    Tries the name directly with zoneinfo first.  If that fails, checks the
    legacy mapping.  Returns the working IANA timezone string, or None if
    the name is unrecognizable.
    """
    if not tz_name:
        return None
    # Try as-is first
    try:
        zoneinfo.ZoneInfo(tz_name)
        return tz_name
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        pass
    # Try legacy mapping
    canonical = _LEGACY_TZ_MAP.get(tz_name)
    if canonical:
        try:
            zoneinfo.ZoneInfo(canonical)
            logger.info("_normalize_tz: Mapped legacy '%s' -> '%s'", tz_name, canonical)
            return canonical
        except (zoneinfo.ZoneInfoNotFoundError, KeyError):
            pass
    return None


# ---------------------------------------------------------------------------
# Internal: timezone resolution from systemframework API
# ---------------------------------------------------------------------------

async def _fetch_owner_timezone_from_api() -> str:
    """
    Fetch the owner user timezone from the systemframework API.
    Resolution: ownSystemArr first, then shareSystemArr, then UTC.
    """
    api_client = get_current_api_client()
    if not api_client:
        logger.warning("No API client available - falling back to UTC")
        return _FALLBACK_TZ

    try:
        framework_data = await api_client.get_system_framework()
        if framework_data.get("status") != "success":
            logger.warning("System framework API error - falling back to UTC")
            return _FALLBACK_TZ

        for system_json_str in framework_data.get("ownSystemArr", []):
            try:
                system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                tz = system.get("timezone")
                if tz:
                    normalized = _normalize_tz(tz)
                    if normalized:
                        return normalized
            except (json.JSONDecodeError, KeyError):
                continue

        for system_json_str in framework_data.get("shareSystemArr", []):
            try:
                system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                tz = system.get("timezone")
                if tz:
                    normalized = _normalize_tz(tz)
                    if normalized:
                        return normalized
            except (json.JSONDecodeError, KeyError):
                continue

    except Exception as e:
        logger.warning(f"Error fetching owner timezone: {e} - falling back to UTC")

    return _FALLBACK_TZ


async def resolve_system_timezone(system_name: Optional[str] = None) -> Tuple[str, str]:
    """
    Resolve timezone for a specific system, or return the owner's default timezone.
    Also resolves the correct display name of the system for API calls.

    Args:
        system_name: Optional system display name or system ID.
                     If None, returns the owner user's default timezone.

    Returns:
        Tuple of (tz_name, resolved_system_name):
            - tz_name: IANA timezone string (e.g. "America/New_York", "UTC")
            - resolved_system_name: The exact systemDisplayName from the API,
              or the original system_name if not found.
    """
    original_name = system_name or ""
    api_client = get_current_api_client()
    if not api_client:
        logger.warning("resolve_system_timezone: No API client available")
        return _FALLBACK_TZ, original_name

    try:
        framework_data = await api_client.get_system_framework()
        if framework_data.get("status") != "success":
            logger.warning("resolve_system_timezone: System framework API returned non-success")
            return _FALLBACK_TZ, original_name

        all_systems_json = framework_data.get("ownSystemArr", []) + framework_data.get("shareSystemArr", [])

        if system_name:
            logger.info("resolve_system_timezone: Looking for system '%s' in %d systems", system_name, len(all_systems_json))
            for system_json_str in all_systems_json:
                try:
                    system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                    display_name = system.get("systemDisplayName", "")
                    system_key = system.get("systemKey", {})
                    sys_name = system_key.get("systemName", "")

                    if (display_name.lower() == system_name.lower()
                            or sys_name.lower() == system_name.lower()):
                        tz = system.get("timezone", _FALLBACK_TZ)
                        resolved_name = display_name or system_name
                        logger.info("resolve_system_timezone: Matched '%s' -> display='%s', tz='%s'",
                                    system_name, resolved_name, tz)
                        normalized = _normalize_tz(tz)
                        if normalized:
                            return normalized, resolved_name
                        else:
                            logger.warning("resolve_system_timezone: Unrecognized timezone '%s' for system '%s'", tz, system_name)
                            return _FALLBACK_TZ, resolved_name
                except (json.JSONDecodeError, TypeError):
                    continue

            # System name was provided but not found - log available names for debugging
            available_names = []
            for system_json_str in all_systems_json:
                try:
                    system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                    dn = system.get("systemDisplayName", "?")
                    sk = system.get("systemKey", {}).get("systemName", "?")
                    available_names.append(f"{dn} (key={sk})")
                except Exception:
                    continue
            logger.warning("resolve_system_timezone: System '%s' not found by exact match. Available: %s",
                           system_name, available_names[:10])

            # Fallback: try case-insensitive contains match
            search_lower = system_name.lower()
            for system_json_str in all_systems_json:
                try:
                    system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                    display_name = system.get("systemDisplayName", "")
                    system_key = system.get("systemKey", {})
                    sys_name = system_key.get("systemName", "")

                    if (search_lower in display_name.lower()
                            or search_lower in sys_name.lower()
                            or display_name.lower() in search_lower
                            or sys_name.lower() in search_lower):
                        tz = system.get("timezone", _FALLBACK_TZ)
                        resolved_name = display_name or system_name
                        logger.info("resolve_system_timezone: Fuzzy-matched '%s' -> display='%s', tz='%s'",
                                    system_name, resolved_name, tz)
                        normalized = _normalize_tz(tz)
                        if normalized:
                            return normalized, resolved_name
                        else:
                            return _FALLBACK_TZ, resolved_name
                except (json.JSONDecodeError, TypeError):
                    continue

            logger.warning("resolve_system_timezone: System '%s' not found even by fuzzy match", system_name)

        # No specific system or not found - return owner default
        for system_json_str in framework_data.get("ownSystemArr", []):
            try:
                system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                tz = system.get("timezone")
                if tz:
                    normalized = _normalize_tz(tz)
                    if normalized:
                        return normalized, original_name
            except Exception:
                continue

        for system_json_str in framework_data.get("shareSystemArr", []):
            try:
                system = json.loads(system_json_str) if isinstance(system_json_str, str) else system_json_str
                tz = system.get("timezone")
                if tz:
                    normalized = _normalize_tz(tz)
                    if normalized:
                        return normalized, original_name
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"Error resolving timezone for system '{system_name}': {e}")

    return _FALLBACK_TZ, original_name


# ---------------------------------------------------------------------------
# Public utility functions used by all tool modules
# ---------------------------------------------------------------------------

def _make_tz(tz_name: str):
    """Create a ZoneInfo object, normalizing legacy names and falling back to UTC on error."""
    normalized = _normalize_tz(tz_name)
    if normalized:
        return zoneinfo.ZoneInfo(normalized)
    return zoneinfo.ZoneInfo("UTC")


def format_timestamp_for_display(timestamp_ms: int, tz_name: str) -> str:
    """
    Format a 13-digit millisecond timestamp for human display.

    IMPORTANT: InsightFinder timestamps are NOT real UTC epochs.
    They represent wall-clock time in the Owner User Timezone, stored
    as if that wall-clock time were UTC.  So we just read the UTC
    face-value and label it with the owner timezone — no conversion.

    Args:
        timestamp_ms: 13-digit millisecond timestamp (owner-tz wall-clock as UTC epoch)
        tz_name: IANA timezone string (e.g. "US/Eastern") — used for labelling only

    Returns:
        Formatted string like "2026-02-12 14:30:00 (US/Eastern)"
    """
    timestamp_ms = int(timestamp_ms) if isinstance(timestamp_ms, str) else timestamp_ms
    # Read the epoch as UTC — that gives us the owner's wall-clock time directly
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({tz_name})"


def _wall_clock_to_fake_utc_ms(dt_aware: datetime) -> int:
    """
    Convert a timezone-aware datetime to the InsightFinder "fake UTC" epoch ms.

    Takes the wall-clock (year, month, day, hour, min, sec) from dt_aware,
    discards the real timezone, and returns the epoch as if that wall-clock
    time were UTC.

    Example: 2026-02-12 00:00:00 EST  ->  epoch for 2026-02-12 00:00:00 UTC
    """
    naive = dt_aware.replace(tzinfo=None)
    return int(calendar.timegm(naive.timetuple()) * 1000)


def get_time_range_ms(tz_name: str, days_back: int = 1) -> Tuple[int, int]:
    """
    Calculate a default time range (start, end) in InsightFinder milliseconds.

    Gets the current wall-clock time in the owner timezone, then produces
    "fake UTC" epoch values (wall-clock treated as UTC).

    Args:
        tz_name: IANA timezone string
        days_back: Number of days to go back (default 1)

    Returns:
        (start_time_ms, end_time_ms)
    """
    tz = _make_tz(tz_name)
    now_local = datetime.now(tz)
    start_local = now_local - timedelta(days=days_back)
    start_ms = _wall_clock_to_fake_utc_ms(start_local)
    end_ms = _wall_clock_to_fake_utc_ms(now_local)
    return start_ms, end_ms


def parse_user_datetime_to_ms(input_str: str, user_tz_name: str) -> int:
    """
    Parse various user datetime formats into InsightFinder epoch milliseconds.

    IMPORTANT: The returned value is a "fake UTC" epoch — wall-clock time in the
    owner timezone encoded as if it were UTC.  This is what the InsightFinder
    backend expects.

    For inputs that already have an explicit timezone (ISO 8601 with Z or offset),
    we first convert to the owner timezone's wall-clock, then encode as fake UTC.

    For inputs without timezone info, we interpret them as being in user_tz already.

    13-digit and 10-digit raw timestamps are passed through unchanged (assumed
    already in InsightFinder format).

    Supports:
        - 13-digit ms string: "1770768600000"
        - 10-digit sec string: "1770768600"
        - ISO 8601 with explicit tz: "2026-02-02T11:00:00Z", "2026-02-02T11:00:00+05:30"
        - ISO 8601 without tz (interpreted in user_tz): "2026-02-02T11:00:00"
        - Date only (midnight in user_tz): "2026-02-02"
        - US format: "02/02/2026"
    """
    input_str = input_str.strip()

    # Already a raw timestamp — pass through (assumed InsightFinder format)
    if input_str.isdigit() and len(input_str) == 13:
        return int(input_str)

    if input_str.isdigit() and len(input_str) == 10:
        return int(input_str) * 1000

    tz = _make_tz(user_tz_name)

    # ISO 8601 with Z suffix — explicit UTC
    if input_str.endswith("Z"):
        try:
            parsed = input_str[:-1] + "+00:00"
            dt_utc = datetime.fromisoformat(parsed.replace(" ", "T", 1))
            # Convert UTC wall-clock to owner tz wall-clock, then fake-UTC-encode
            dt_local = dt_utc.astimezone(tz)
            return _wall_clock_to_fake_utc_ms(dt_local)
        except ValueError:
            pass

    # Check for explicit offset like +05:30 or -04:00
    offset_match = re.search(r'[+-]\d{2}:\d{2}$', input_str)
    if offset_match:
        try:
            dt_with_offset = datetime.fromisoformat(input_str.replace(" ", "T", 1))
            # Convert to owner tz wall-clock, then fake-UTC-encode
            dt_local = dt_with_offset.astimezone(tz)
            return _wall_clock_to_fake_utc_ms(dt_local)
        except ValueError:
            pass

    # ISO datetime without tz — interpret in user_tz, encode as fake UTC
    if "T" in input_str or (len(input_str) > 10 and " " in input_str):
        try:
            parsed = input_str.replace("T", " ", 1)
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt_naive = datetime.strptime(parsed, fmt)
                    # Treat this naive time as being in the owner tz
                    # Encode wall-clock directly as fake UTC
                    return int(calendar.timegm(dt_naive.timetuple()) * 1000)
                except ValueError:
                    continue
        except Exception:
            pass

    # Date only — midnight in user_tz, encode as fake UTC
    date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", input_str)
    if date_match:
        y, m, d = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
        dt_naive = datetime(y, m, d, 0, 0, 0)
        return int(calendar.timegm(dt_naive.timetuple()) * 1000)

    # US format MM/DD/YYYY — midnight in user_tz, encode as fake UTC
    us_match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", input_str)
    if us_match:
        m, d, y = int(us_match.group(1)), int(us_match.group(2)), int(us_match.group(3))
        dt_naive = datetime(y, m, d, 0, 0, 0)
        return int(calendar.timegm(dt_naive.timetuple()) * 1000)

    raise ValueError(
        f"Cannot parse datetime: '{input_str}'. "
        f"Supported formats: ISO 8601, YYYY-MM-DD, MM/DD/YYYY, or 13-digit milliseconds"
    )


def convert_to_ms(timestamp: Optional[Union[str, int, float]], param_name: str = "timestamp", tz_name: str = "UTC") -> Optional[int]:
    """
    Convert a timestamp parameter to InsightFinder fake-UTC milliseconds.

    Accepts any human-readable format that parse_user_datetime_to_ms() supports:
        - "2026-02-12T11:05:00"       (ISO without offset — treated as owner tz)
        - "2026-02-12T11:05:00Z"      (ISO with Z — converted from UTC to owner tz)
        - "2026-02-12T11:05:00-05:00" (ISO with offset — converted to owner tz)
        - "2026-02-12"                (date only — midnight in owner tz)
        - "02/12/2026"                (US format MM/DD/YYYY)
        - "1770768600000"             (13-digit ms — pass-through)
        - 1770768600000               (int — pass-through)

    Args:
        timestamp: The timestamp value (int, str, float, or None)
        param_name: The name of the parameter (for error messages)
        tz_name: Owner timezone name for interpreting naive datetimes

    Returns:
        int or None: The timestamp as InsightFinder fake-UTC milliseconds, or None if input was None

    Raises:
        ValueError: If the timestamp cannot be parsed
    """
    if timestamp is None:
        return None

    if isinstance(timestamp, (int, float)):
        return int(timestamp)

    if isinstance(timestamp, str):
        timestamp = timestamp.strip()
        if not timestamp:
            return None
        try:
            return parse_user_datetime_to_ms(timestamp, tz_name)
        except ValueError:
            raise ValueError(
                f"Invalid {param_name}: cannot parse '{timestamp}'. "
                f"Accepted formats: '2026-02-12T11:05:00', '2026-02-12', '02/12/2026', "
                f"or 13-digit milliseconds."
            )

    raise ValueError(f"Invalid {param_name}: must be a string or integer, got {type(timestamp).__name__}")


# ---------------------------------------------------------------------------
# Legacy-compatible helper functions used by tool modules
# ---------------------------------------------------------------------------

def get_timezone_aware_time_range_ms(days_back: int = 1) -> Tuple[int, int]:
    """
    Synchronous fallback: get default time range using UTC as the owner tz.
    Since we assume UTC here, the wall-clock IS UTC, so the fake-UTC epoch
    is the same as the real UTC epoch.
    Tools should prefer calling get_default_time_range_ms() (async).
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    return int(calendar.timegm(start.timetuple()) * 1000), int(calendar.timegm(now.timetuple()) * 1000)


async def get_default_time_range_ms(
    days_back: int = 1,
    system_name: Optional[str] = None
) -> Tuple[int, int, str]:
    """
    Async function that resolves the owner timezone and returns a default
    time range plus the resolved timezone name.

    Returns:
        (start_ms, end_ms, tz_name)
    """
    tz_name, _resolved_name = await resolve_system_timezone(system_name)
    start_ms, end_ms = get_time_range_ms(tz_name, days_back)
    return start_ms, end_ms, tz_name


def format_timestamp_in_user_timezone(timestamp_ms: int, tz_name: str = "UTC") -> str:
    """Format a timestamp for display. Pass resolved tz_name for proper timezone."""
    return format_timestamp_for_display(timestamp_ms, tz_name)


def format_api_timestamp_corrected(timestamp_ms: int, tz_name: str = "UTC") -> str:
    """Format an API-returned timestamp for display."""
    return format_timestamp_for_display(timestamp_ms, tz_name)


# ---------------------------------------------------------------------------
# MCP Tools: Exposed to LLMs
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def get_current_datetime() -> str:
    """Get the current date and time in the owner's timezone.

    Resolves the timezone from the InsightFinder system framework API,
    then returns the current time in that timezone.
    Returns timestamps in InsightFinder millisecond format for query compatibility.
    """
    tz_name = await _fetch_owner_timezone_from_api()
    tz = _make_tz(tz_name)

    now_local = datetime.now(tz)
    current_time_ms = _wall_clock_to_fake_utc_ms(now_local)

    result = {
        "owner_timezone": tz_name,
        "current_owner_time": now_local.strftime("%Y-%m-%d %H:%M:%S") + f" ({tz_name})",
        "current_time_milliseconds": current_time_ms,
        "formatted_time": {
            "human_readable": now_local.strftime("%Y-%m-%d %H:%M:%S") + f" ({tz_name})",
            "date_only": now_local.strftime("%Y-%m-%d"),
            "time_only": now_local.strftime("%H:%M:%S"),
        },
        "relative_times_milliseconds": {
            "1_hour_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(hours=1)),
            "6_hours_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(hours=6)),
            "12_hours_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(hours=12)),
            "24_hours_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(hours=24)),
            "1_week_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(days=7)),
            "1_month_ago": _wall_clock_to_fake_utc_ms(now_local - timedelta(days=30)),
        },
    }

    return json.dumps(result, indent=2)


def parse_datetime_string_to_ms(timestamp_str: str) -> int:
    """
    Parses a datetime string (ISO 8601 or similar) into a millisecond timestamp.
    Raises ValueError if parsing fails.
    """
    timestamp_str = timestamp_str.strip()
    
    # Handle 'Z' suffix (UTC indicator)
    if timestamp_str.endswith('Z'):
        iso_timestamp_parsed = timestamp_str[:-1] + '+00:00'
    else:
        iso_timestamp_parsed = timestamp_str
    
    # Handle space separator (replace with T)
    if ' ' in iso_timestamp_parsed and 'T' not in iso_timestamp_parsed:
        iso_timestamp_parsed = iso_timestamp_parsed.replace(' ', 'T', 1)
    
    # Parse the timestamp
    dt = datetime.fromisoformat(iso_timestamp_parsed)
    
    # If no timezone info, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # Convert to UTC if not already
    dt_utc = dt.astimezone(timezone.utc)
    
    # Convert to milliseconds timestamp
    timestamp_ms = int(dt_utc.timestamp() * 1000)
    
    # Validate reasonable date range (1970-2286)
    if dt_utc.year < 1970 or dt_utc.year > 2286:
        raise ValueError(f"Timestamp represents an unrealistic date: {dt_utc.year}")
        
    return timestamp_ms

from typing import Union, Optional

def parse_timestamp_argument(timestamp: Union[int, str, None]) -> Optional[int]:
    """
    Parses a timestamp argument which can be an integer (ms) or a string (ISO/Human).
    Returns integer timestamp in milliseconds or None if input is None.
    Raises ValueError if string parsing fails.
    """
    if timestamp is None:
        return None
        
    if isinstance(timestamp, int):
        return timestamp
        
    if isinstance(timestamp, str):
        try:
            return int(timestamp)
        except ValueError:
            return parse_datetime_string_to_ms(timestamp)
            
    raise ValueError(f"Invalid timestamp type: {type(timestamp)}")

@mcp_server.tool()
async def get_time_range(hours_back: int = 24) -> str:
    """Get start and end time range in InsightFinder milliseconds for queries.

    Uses the owner's timezone so that time ranges align with the
    system's concept of time. Returns timestamps in InsightFinder format.

    Args:
        hours_back: Number of hours to go back from current time (default: 24)
    """
    tz_name = await _fetch_owner_timezone_from_api()
    tz = _make_tz(tz_name)

    now_local = datetime.now(tz)
    start_local = now_local - timedelta(hours=hours_back)

    end_ms = _wall_clock_to_fake_utc_ms(now_local)
    start_ms = _wall_clock_to_fake_utc_ms(start_local)

    result = {
        "owner_timezone": tz_name,
        "query_period_hours": hours_back,
        "end_time": {
            "milliseconds": end_ms,
            "owner_time": now_local.strftime("%Y-%m-%d %H:%M:%S") + f" ({tz_name})",
        },
        "start_time": {
            "milliseconds": start_ms,
            "owner_time": start_local.strftime("%Y-%m-%d %H:%M:%S") + f" ({tz_name})",
        },
    }

    return json.dumps(result, indent=2)


@mcp_server.tool()
async def get_date_range_for_timezone(date_input: str, timezone_input: Optional[str] = None) -> str:
    """Get start and end timestamps for a specific date in the owner's timezone.

    Interprets the given date in the owner's timezone and returns
    midnight-to-end-of-day timestamps in InsightFinder millisecond format.

    Args:
        date_input: Date in supported formats:
                   - "2026-02-12" (ISO format)
                   - "02/12/2026" (US format MM/DD/YYYY)
                   - "Feb 12, 2026" or "February 12, 2026"
        timezone_input: Optional explicit timezone override (e.g. "UTC", "US/Mountain").
                       If not provided, uses owner default timezone.
    """
    if timezone_input:
        normalized = _normalize_tz(timezone_input)
        if normalized:
            tz_name = normalized
        else:
            return json.dumps({
                "error": f"Invalid timezone: '{timezone_input}'",
                "suggestion": "Use IANA timezone names like 'US/Eastern', 'UTC', 'America/New_York'"
            }, indent=2)
    else:
        tz_name = await _fetch_owner_timezone_from_api()

    date_input = date_input.strip()

    target_date = None
    try:
        iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", date_input)
        if iso_match:
            y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
            target_date = datetime(y, m, d).date()
        elif re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", date_input):
            us_match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_input)
            if us_match:
                m, d, y = int(us_match.group(1)), int(us_match.group(2)), int(us_match.group(3))
                target_date = datetime(y, m, d).date()
        else:
            month_names = {
                "jan": 1, "january": 1, "feb": 2, "february": 2,
                "mar": 3, "march": 3, "apr": 4, "april": 4,
                "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
                "aug": 8, "august": 8, "sep": 9, "september": 9,
                "oct": 10, "october": 10, "nov": 11, "november": 11,
                "dec": 12, "december": 12,
            }
            written_match = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", date_input)
            if written_match:
                month_name = written_match.group(1).lower()
                day = int(written_match.group(2))
                year = int(written_match.group(3))
                if month_name in month_names:
                    target_date = datetime(year, month_names[month_name], day).date()
                else:
                    raise ValueError(f"Unknown month: {month_name}")
            else:
                raise ValueError(f"Unsupported date format: {date_input}")
    except Exception as e:
        return json.dumps({
            "error": f"Cannot parse date: '{date_input}'",
            "parse_error": str(e),
            "supported_formats": ["2026-02-12 (ISO)", "02/12/2026 (US MM/DD/YYYY)", "Feb 12, 2026"],
        }, indent=2)

    if target_date is None:
        return json.dumps({
            "error": f"Cannot parse date: '{date_input}'",
            "supported_formats": ["2026-02-12 (ISO)", "02/12/2026 (US MM/DD/YYYY)", "Feb 12, 2026"],
        }, indent=2)

    # Build naive datetimes for start/end of day — these represent wall-clock
    # in the owner timezone.  Encode as fake UTC for the InsightFinder API.
    start_naive = datetime.combine(target_date, datetime.min.time())          # 00:00:00
    end_naive = datetime.combine(target_date, datetime.max.time().replace(microsecond=999000))  # 23:59:59.999

    start_ms = int(calendar.timegm(start_naive.timetuple()) * 1000)
    end_ms = int(calendar.timegm(end_naive.timetuple()) * 1000)

    result = {
        "input_date": date_input,
        "parsed_date": target_date.strftime("%Y-%m-%d"),
        "timezone": tz_name,
        "start_time": {
            "milliseconds": start_ms,
            "owner_time": format_timestamp_for_display(start_ms, tz_name),
        },
        "end_time": {
            "milliseconds": end_ms,
            "owner_time": format_timestamp_for_display(end_ms, tz_name),
        },
    }

    return json.dumps(result, indent=2)


@mcp_server.tool()
async def convert_timestamp(
    timestamp_or_datetime: str,
    from_timezone: Optional[str] = None,
    to_timezone: Optional[str] = None
) -> str:
    """Convert a timestamp or datetime, or verify a 13-digit InsightFinder timestamp.

    InsightFinder timestamps are wall-clock time in the owner timezone
    encoded as if it were UTC.  This tool decodes and displays them.

    Examples:
        convert_timestamp("1770768600000")  -> shows wall-clock in owner timezone
        convert_timestamp("2026-02-02T11:00:00Z")  -> interprets as UTC, shows in owner tz
        convert_timestamp("2026-02-02 11:00:00", from_timezone="UTC", to_timezone="US/Eastern")

    Args:
        timestamp_or_datetime: The value to convert (13-digit ms, ISO 8601, date, etc.)
        from_timezone: Source timezone (default: owner timezone)
        to_timezone: Target timezone for display (default: owner timezone)
    """
    owner_tz_name = await _fetch_owner_timezone_from_api()
    source_tz_name = from_timezone or owner_tz_name
    target_tz_name = to_timezone or owner_tz_name

    for tz_label, tz_val in [("from_timezone", source_tz_name), ("to_timezone", target_tz_name)]:
        normalized = _normalize_tz(tz_val)
        if not normalized:
            return json.dumps({
                "valid": False,
                "error": f"Invalid {tz_label}: '{tz_val}'",
                "suggestion": "Use IANA timezone names like 'US/Eastern', 'UTC', 'America/New_York'",
            }, indent=2)

    # Use normalized timezone names
    source_tz_name = _normalize_tz(source_tz_name) or source_tz_name
    target_tz_name = _normalize_tz(target_tz_name) or target_tz_name

    try:
        timestamp_ms = parse_user_datetime_to_ms(timestamp_or_datetime, source_tz_name)
    except ValueError as e:
        return json.dumps({
            "valid": False,
            "error": str(e),
            "provided_value": timestamp_or_datetime,
        }, indent=2)

    # The timestamp_ms is a "fake UTC" epoch — the face-value IS the
    # wall-clock time in the owner timezone.  Just read it as UTC.
    dt_wall = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    if dt_wall.year < 1970 or dt_wall.year > 2286:
        return json.dumps({
            "valid": False,
            "error": f"Timestamp out of range - year {dt_wall.year}",
            "provided_value": timestamp_or_datetime,
        }, indent=2)

    result = {
        "valid": True,
        "input": timestamp_or_datetime,
        "timestamp_ms": timestamp_ms,
        "owner_timezone": owner_tz_name,
        "wall_clock_time": dt_wall.strftime("%Y-%m-%d %H:%M:%S") + f" ({owner_tz_name})",
        "date": dt_wall.strftime("%Y-%m-%d"),
        "time": dt_wall.strftime("%H:%M:%S"),
        "weekday": dt_wall.strftime("%A"),
        "note": "InsightFinder timestamps represent wall-clock time in the owner timezone, not real UTC.",
    }

    return json.dumps(result, indent=2)
