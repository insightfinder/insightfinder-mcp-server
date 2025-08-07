# In your MCP server (insightfinder_mcp_server)
from datetime import datetime, timezone, timedelta
from ..server import mcp_server

import zoneinfo
import json
import os

@mcp_server.tool()
def get_current_datetime() -> str:
    """Get the current date and time in various formats with timezone support.
    
    Uses the TZ environment variable to determine the user's timezone.
    If TZ is not set, defaults to UTC.
    Returns timestamps in milliseconds for query compatibility.
    """
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
        tz_name = 'UTC'
    
    # Get current time in UTC and user's timezone
    now_utc = datetime.now(timezone.utc)
    now_user_tz = now_utc.astimezone(user_timezone)
    
    # Convert to milliseconds (multiply by 1000)
    current_time_ms = int(now_utc.timestamp() * 1000)
    
    result = {
        'user_timezone': tz_name,
        'current_utc': now_utc.isoformat(),
        'current_user_time': now_user_tz.isoformat(),
        'current_time_milliseconds': current_time_ms,
        'current_time_seconds': int(now_utc.timestamp()),
        'formatted_user_time': {
            'human_readable': now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'date_only': now_user_tz.strftime('%Y-%m-%d'),
            'time_only': now_user_tz.strftime('%H:%M:%S')
        }
    }
    
    # Add relative time helpers in milliseconds
    result['relative_times_milliseconds'] = {
        '1_hour_ago': int((now_utc - timedelta(hours=1)).timestamp() * 1000),
        '30_minutes_ago': int((now_utc - timedelta(minutes=30)).timestamp() * 1000),
        '6_hours_ago': int((now_utc - timedelta(hours=6)).timestamp() * 1000),
        '12_hours_ago': int((now_utc - timedelta(hours=12)).timestamp() * 1000),
        '24_hours_ago': int((now_utc - timedelta(hours=24)).timestamp() * 1000),
        '1_week_ago': int((now_utc - timedelta(days=7)).timestamp() * 1000),
        '1_month_ago': int((now_utc - timedelta(days=30)).timestamp() * 1000)
    }
    
    # Add relative time helpers in seconds (for backward compatibility)
    result['relative_times_seconds'] = {
        '1_hour_ago': int((now_utc - timedelta(hours=1)).timestamp()),
        '30_minutes_ago': int((now_utc - timedelta(minutes=30)).timestamp()),
        '24_hours_ago': int((now_utc - timedelta(hours=24)).timestamp()),
        '1_week_ago': int((now_utc - timedelta(days=7)).timestamp())
    }
    
    return json.dumps(result, indent=2)

@mcp_server.tool()
def get_time_range(hours_back: int = 24) -> str:
    """Get start and end time range in milliseconds for queries.
    
    Args:
        hours_back: Number of hours to go back from current time (default: 24)
    
    Returns time range based on user's timezone (from TZ env var) or UTC.
    Provides both milliseconds and seconds timestamps for compatibility.
    """
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
        tz_name = 'UTC'
    
    # Get current time and calculate start time
    now_utc = datetime.now(timezone.utc)
    start_time_utc = now_utc - timedelta(hours=hours_back)
    
    # Convert to user's timezone for display
    now_user_tz = now_utc.astimezone(user_timezone)
    start_time_user_tz = start_time_utc.astimezone(user_timezone)
    
    result = {
        'timezone': tz_name,
        'query_period_hours': hours_back,
        'end_time': {
            'milliseconds': int(now_utc.timestamp() * 1000),
            'seconds': int(now_utc.timestamp()),
            'iso_utc': now_utc.isoformat(),
            'iso_user_tz': now_user_tz.isoformat(),
            'human_readable': now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')
        },
        'start_time': {
            'milliseconds': int(start_time_utc.timestamp() * 1000),
            'seconds': int(start_time_utc.timestamp()),
            'iso_utc': start_time_utc.isoformat(),
            'iso_user_tz': start_time_user_tz.isoformat(),
            'human_readable': start_time_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')
        }
    }
    
    return json.dumps(result, indent=2)

def get_timezone_aware_timestamp_ms() -> int:
    """Get current timestamp in milliseconds, timezone-aware based on TZ env var."""
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
    
    # Get current time in UTC (since timestamps are always UTC)
    now_utc = datetime.now(timezone.utc)
    return int(now_utc.timestamp() * 1000)

def get_timezone_aware_time_range_ms(hours_back: int = 24) -> tuple[int, int]:
    """Get start and end timestamps in milliseconds for a time range in user's timezone."""
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
    
    # Get current time in user's timezone
    now_user_tz = datetime.now(user_timezone)
    
    # Calculate start time by going back the specified hours in user's timezone
    start_time_user_tz = now_user_tz - timedelta(hours=hours_back)
    
    # Convert both times to UTC timestamps in milliseconds
    end_time_ms = int(now_user_tz.timestamp() * 1000)
    start_time_ms = int(start_time_user_tz.timestamp() * 1000)
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIME] get_timezone_aware_time_range_ms({hours_back}h):", file=sys.stderr)
        print(f"  Timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        print(f"  Current time (local): {now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  Start time (local): {start_time_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  End timestamp (ms): {end_time_ms}", file=sys.stderr)
        print(f"  Start timestamp (ms): {start_time_ms}", file=sys.stderr)
    
    return start_time_ms, end_time_ms

def get_today_time_range_ms() -> tuple[int, int]:
    """Get start and end timestamps for 'today' in the user's timezone."""
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
    
    # Get current time in user's timezone
    now_user_tz = datetime.now(user_timezone)
    
    # Get start of today (midnight) in user's timezone
    start_of_today = now_user_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Convert to UTC timestamps in milliseconds
    start_time_ms = int(start_of_today.timestamp() * 1000)
    end_time_ms = int(now_user_tz.timestamp() * 1000)
    
    return start_time_ms, end_time_ms

def format_timestamp_in_user_timezone(timestamp_ms: int, assume_utc: bool = True) -> str:
    """Format a timestamp in the user's timezone based on TZ env var.
    
    Args:
        timestamp_ms: Timestamp in milliseconds
        assume_utc: If True, treat timestamp as UTC and convert to user timezone.
                   If False, treat timestamp as already in user timezone.
    """
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
        tz_name = 'UTC'
    
    if assume_utc:
        # Convert UTC timestamp to user's timezone (current behavior)
        dt_utc = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        dt_user_tz = dt_utc.astimezone(user_timezone)
    else:
        # Treat timestamp as already in user's timezone (new option)
        dt_user_tz = datetime.fromtimestamp(timestamp_ms / 1000, tz=user_timezone)
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIMESTAMP] format_timestamp_in_user_timezone({timestamp_ms}, assume_utc={assume_utc}):", file=sys.stderr)
        print(f"  Target timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        if assume_utc:
            print(f"  As UTC: {dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  Result: {dt_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
    
    # Format with timezone abbreviation - for better debugging, also show offset
    tz_str = dt_user_tz.strftime('%Z')
    if not tz_str or tz_str == dt_user_tz.strftime('%z'):  # If %Z doesn't work well
        # Fallback to showing offset
        offset = dt_user_tz.strftime('%z')
        if offset:
            tz_str = f"UTC{offset[:3]}:{offset[3:]}"
        else:
            tz_str = "UTC"
    
    return dt_user_tz.strftime(f'%Y-%m-%d %H:%M:%S {tz_str}')

def format_timestamp_no_conversion(timestamp_ms: int) -> str:
    """Format a timestamp without timezone conversion - use when API returns local time."""
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
    
    # Treat timestamp as already in user's timezone
    dt_user_tz = datetime.fromtimestamp(timestamp_ms / 1000, tz=user_timezone)
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIMESTAMP] format_timestamp_no_conversion({timestamp_ms}):", file=sys.stderr)
        print(f"  Target timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        print(f"  Result: {dt_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
    
    # Format with timezone abbreviation
    tz_str = dt_user_tz.strftime('%Z')
    if not tz_str or tz_str == dt_user_tz.strftime('%z'):
        # Fallback to showing offset
        offset = dt_user_tz.strftime('%z')
        if offset:
            tz_str = f"UTC{offset[:3]}:{offset[3:]}"
        else:
            tz_str = "UTC"
    
    return dt_user_tz.strftime(f'%Y-%m-%d %H:%M:%S {tz_str}')

def format_api_timestamp_corrected(timestamp_ms: int) -> str:
    """Format an API timestamp with 4-hour correction to show correct local time.
    
    The InsightFinder API returns timestamps that are 4 hours behind the actual local time.
    This function adds 4 hours before formatting to display the correct time.
    """
    # Add 4 hours (14400000 ms) to correct the API timestamp
    corrected_timestamp = timestamp_ms + (4 * 60 * 60 * 1000)
    
    # Get timezone from environment variable, default to UTC
    tz_name = normalize_timezone(os.getenv('TZ', 'UTC'))
    
    try:
        # Try to parse the timezone
        if tz_name.upper() == 'UTC':
            user_timezone = timezone.utc
        else:
            user_timezone = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Fallback to UTC if timezone is invalid
        user_timezone = timezone.utc
    
    # Convert corrected UTC timestamp to user's timezone
    dt_utc = datetime.fromtimestamp(corrected_timestamp / 1000, tz=timezone.utc)
    dt_user_tz = dt_utc.astimezone(user_timezone)
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIMESTAMP] format_api_timestamp_corrected({timestamp_ms}):", file=sys.stderr)
        print(f"  Original timestamp: {timestamp_ms}", file=sys.stderr)
        print(f"  Corrected timestamp (+4h): {corrected_timestamp}", file=sys.stderr)
        print(f"  Target timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        print(f"  Corrected as UTC: {dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  Result: {dt_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
    
    # Format with timezone abbreviation
    tz_str = dt_user_tz.strftime('%Z')
    if not tz_str or tz_str == dt_user_tz.strftime('%z'):
        # Fallback to showing offset
        offset = dt_user_tz.strftime('%z')
        if offset:
            tz_str = f"UTC{offset[:3]}:{offset[3:]}"
        else:
            tz_str = "UTC"
    
    return dt_user_tz.strftime(f'%Y-%m-%d %H:%M:%S {tz_str}')

def normalize_timezone(tz_name: str) -> str:
    """Normalize timezone names and abbreviations to proper timezone identifiers."""
    if not tz_name:
        return 'UTC'
    
    tz_upper = tz_name.upper()
    
    # Handle common timezone abbreviations
    abbreviation_map = {
        'EDT': 'America/New_York',
        'EST': 'America/New_York', 
        'CDT': 'America/Chicago',
        'CST': 'America/Chicago',
        'MDT': 'America/Denver',
        'MST': 'America/Denver',
        'PDT': 'America/Los_Angeles',
        'PST': 'America/Los_Angeles',
        'UTC': 'UTC',
        'GMT': 'UTC'
    }
    
    if tz_upper in abbreviation_map:
        return abbreviation_map[tz_upper]
    
    # Return as-is for proper timezone names
    return tz_name