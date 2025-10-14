# In your MCP server (insightfinder_mcp_server)
from datetime import datetime, timezone, timedelta
from ..server import mcp_server

import zoneinfo
import json
import os

@mcp_server.tool()
def get_current_datetime() -> str:
    """Get the current date and time in UTC only.
    
    Always uses UTC timezone to avoid timezone conversion confusion.
    Returns timestamps in milliseconds for query compatibility.
    
    ⚠️ IMPORTANT: Call this tool FIRST before any date/time calculations!
    See resource 'time-tools://usage-guide' for detailed usage patterns.
    """
    # Always use UTC
    user_timezone = timezone.utc
    tz_name = 'UTC'
    
    # Get current time in UTC only
    now_utc = datetime.now(timezone.utc)
    now_user_tz = now_utc  # Same as UTC
    
    # Convert to milliseconds (multiply by 1000) and round to nearest second
    current_time_ms = int(now_utc.timestamp()) * 1000
    
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
    
    # Add relative time helpers in milliseconds (rounded to nearest second)
    result['relative_times_milliseconds'] = {
        '1_hour_ago': int((now_utc - timedelta(hours=1)).timestamp()) * 1000,
        '30_minutes_ago': int((now_utc - timedelta(minutes=30)).timestamp()) * 1000,
        '6_hours_ago': int((now_utc - timedelta(hours=6)).timestamp()) * 1000,
        '12_hours_ago': int((now_utc - timedelta(hours=12)).timestamp()) * 1000,
        '24_hours_ago': int((now_utc - timedelta(hours=24)).timestamp()) * 1000,
        '1_week_ago': int((now_utc - timedelta(days=7)).timestamp()) * 1000,
        '1_month_ago': int((now_utc - timedelta(days=30)).timestamp()) * 1000
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
    
    Returns time range in UTC only to avoid timezone conversion issues.
    Provides both milliseconds and seconds timestamps for compatibility.
    
    ⚠️ NOTE: Use get_date_range_utc() for full calendar days (e.g., "yesterday").
    This tool is for rolling time windows (e.g., "last 6 hours").
    See resource 'time-tools://usage-guide' for correct usage patterns.
    """
    # Always use UTC
    user_timezone = timezone.utc
    tz_name = 'UTC'
    
    # Get current time and calculate start time
    now_utc = datetime.now(timezone.utc)
    start_time_utc = now_utc - timedelta(hours=hours_back)
    
    # No timezone conversion needed - everything is UTC
    now_user_tz = now_utc
    start_time_user_tz = start_time_utc
    
    result = {
        'timezone': tz_name,
        'query_period_hours': hours_back,
        'end_time': {
            'milliseconds': int(now_utc.timestamp()) * 1000,
            'seconds': int(now_utc.timestamp()),
            'iso_utc': now_utc.isoformat(),
            'iso_user_tz': now_user_tz.isoformat(),
            'human_readable': now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')
        },
        'start_time': {
            'milliseconds': int(start_time_utc.timestamp()) * 1000,
            'seconds': int(start_time_utc.timestamp()),
            'iso_utc': start_time_utc.isoformat(),
            'iso_user_tz': start_time_user_tz.isoformat(),
            'human_readable': start_time_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')
        }
    }
    
    return json.dumps(result, indent=2)

def get_timezone_aware_time_range_ms(days_back: int = 1) -> tuple[int, int]:
    """Get start and end timestamps in milliseconds for a time range in UTC.
    
    Args:
        days_back: Number of days to go back from current day (default: 1)
                  days_back=0 means today only
                  days_back=1 means from yesterday midnight to end of today
    
    Returns:
        tuple: (start_time_ms, end_time_ms) where:
               - start_time_ms is midnight of the day N days back in UTC
               - end_time_ms is end of current day (23:59:59.999) in UTC
    """
    # Always use UTC for time range calculation
    tz_name = 'UTC'
    user_timezone = timezone.utc
    
    # Get current time in UTC
    now_user_tz = datetime.now(user_timezone)
    
    # Calculate start time: midnight of the day N days back
    start_day = now_user_tz - timedelta(days=days_back)
    start_time_user_tz = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Calculate end time: end of current day (23:59:59.999)
    end_time_user_tz = now_user_tz.replace(hour=23, minute=59, second=59, microsecond=999000)
    
    # Convert both times to UTC timestamps in milliseconds (rounded to nearest second)
    start_time_ms = int(start_time_user_tz.timestamp()) * 1000
    end_time_ms = int(end_time_user_tz.timestamp()) * 1000
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIME] get_timezone_aware_time_range_ms({days_back} days):", file=sys.stderr)
        print(f"  Timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        print(f"  Current time (UTC): {now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  Start time (UTC): {start_time_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  End time (UTC): {end_time_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        print(f"  Start timestamp (ms): {start_time_ms}", file=sys.stderr)
        print(f"  End timestamp (ms): {end_time_ms}", file=sys.stderr)
    
    return start_time_ms, end_time_ms

def format_timestamp_in_user_timezone(timestamp_ms: int, assume_utc: bool = True) -> str:
    """Format a timestamp in UTC only to avoid timezone conversion confusion.
    
    Args:
        timestamp_ms: Timestamp in milliseconds
        assume_utc: Ignored - always treats timestamp as UTC
    """
    # Always use UTC
    tz_name = 'UTC'
    user_timezone = timezone.utc
    
    # Always treat timestamp as UTC and format as UTC
    dt_utc = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    dt_user_tz = dt_utc  # Same as UTC
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        print(f"[DEBUG TIMESTAMP] format_timestamp_in_user_timezone({timestamp_ms}, assume_utc={assume_utc}):", file=sys.stderr)
        print(f"  Target timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
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

def format_api_timestamp_corrected(timestamp_ms: int) -> str:
    """Format an API timestamp in UTC (no correction needed).
    
    Since we're using UTC only now, no timezone correction is needed.
    """
    # No correction - just use UTC
    corrected_timestamp = timestamp_ms
    
    # Always use UTC
    tz_name = 'UTC'
    user_timezone = timezone.utc
    
    # Convert UTC timestamp directly
    dt_utc = datetime.fromtimestamp(corrected_timestamp / 1000, tz=timezone.utc)
    dt_user_tz = dt_utc  # Same as UTC
    
    # Debug logging
    from ...config.settings import settings
    if settings.ENABLE_DEBUG_MESSAGES:
        import sys
        # print(f"[DEBUG TIMESTAMP] format_api_timestamp_corrected({timestamp_ms}):", file=sys.stderr)
        # print(f"  Original timestamp: {timestamp_ms}", file=sys.stderr)
        # print(f"  Target timezone: {tz_name} -> {user_timezone}", file=sys.stderr)
        # print(f"  Corrected as UTC: {dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
        # print(f"  Result: {dt_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)
    
    # Format with timezone abbreviation (always UTC)
    tz_str = "UTC"
    
    return dt_user_tz.strftime(f'%Y-%m-%d %H:%M:%S {tz_str}')

@mcp_server.tool()
def get_date_range_utc(date_input: str) -> str:
    """Get start and end time range in UTC for a specific date.
    
    Args:
        date_input: Date in supported formats:
                   - "2024-08-21" (ISO format)
                   - "2024-08-21T10:30:00" (ISO datetime - time ignored)
                   - "08/21/2024" or "8/21/2024" (US format MM/DD/YYYY)
                   - "Aug 21, 2024" or "August 21, 2024" (US written format)
    
    Returns:
        JSON with start_time (00:00:00 UTC) and end_time (23:59:59.999 UTC) 
        in both milliseconds and seconds timestamps, plus human-readable formats.
    
    ⚠️ USAGE: For queries like "yesterday's data", "data from Aug 21", etc.
    First call get_current_datetime() to know today's date, then calculate the target date.
    See resource 'time-tools://examples' for detailed examples.
    """
    import re
    
    date_input = date_input.strip()
    
    # Get current year
    current_year = datetime.now().year
    
    try:
        # Parse different date formats
        target_date = None
        
        # 1. ISO format: 2024-08-21 or 2024-08-21T10:30:00 (ignore year, use current)
        iso_match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', date_input)
        if iso_match:
            _, month, day = map(int, iso_match.groups())  # Ignore year
            target_date = datetime(current_year, month, day).date()
        
        # 2. US format: MM/DD/YYYY (08/21/2024 or 8/21/2024) (ignore year, use current)
        elif re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', date_input):
            us_match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_input)
            if us_match:
                month, day, _ = map(int, us_match.groups())  # Ignore year
                target_date = datetime(current_year, month, day).date()
        
        # 3. US written format: Aug 21, 2024 or August 21, 2024 (ignore year, use current)
        else:
            # Month name mapping
            month_names = {
                'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
                'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
                'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
                'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12
            }
            
            # Try US written format: Month DD, YYYY (ignore year, use current)
            written_match = re.match(r'^([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})$', date_input)
            if written_match:
                month_name, day, _ = written_match.groups()  # Ignore year
                month_name = month_name.lower()
                if month_name in month_names:
                    month = month_names[month_name]
                    target_date = datetime(current_year, month, int(day)).date()
                else:
                    raise ValueError(f"Unknown month name: {month_name}")
            else:
                raise ValueError(f"Unsupported date format: {date_input}")
                    
    except Exception as e:
            return json.dumps({
                'error': f"Invalid date format: {date_input}",
                'supported_formats': [
                    "2024-08-21 (ISO format)",
                    "2024-08-21T10:30:00 (ISO datetime)",
                    "08/21/2024 (US format MM/DD/YYYY)", 
                    "8/21/2024 (US format M/D/YYYY)",
                    "Aug 21, 2024 (US written format)",
                    "August 21, 2024 (US written format)"
                ],
                'parse_error': str(e)
            }, indent=2)
    
    # Check if we successfully parsed a date
    if target_date is None:
        return json.dumps({
            'error': f"Failed to parse date: {date_input}",
            'supported_formats': [
                "2024-08-21 (ISO format)",
                "2024-08-21T10:30:00 (ISO datetime)",
                "08/21/2024 (US format MM/DD/YYYY)", 
                "8/21/2024 (US format M/D/YYYY)",
                "Aug 21, 2024 (US written format)",
                "August 21, 2024 (US written format)"
            ]
        }, indent=2)
    
    # Create start and end times in UTC
    start_time_utc = datetime.combine(target_date, datetime.min.time(), timezone.utc)
    end_time_utc = datetime.combine(target_date, datetime.max.time(), timezone.utc)
    # Round end time to 23:59:59.999 for cleaner milliseconds
    end_time_utc = end_time_utc.replace(microsecond=999000)
    
    # Convert to timestamps
    start_time_ms = int(start_time_utc.timestamp()) * 1000
    end_time_ms = int(end_time_utc.timestamp()) * 1000
    start_time_seconds = int(start_time_utc.timestamp())
    end_time_seconds = int(end_time_utc.timestamp())
    
    result = {
        'input_date': date_input,
        'parsed_date': target_date.strftime('%Y-%m-%d'),
        'timezone': 'UTC',
        'start_time': {
            'milliseconds': start_time_ms,
            'seconds': start_time_seconds,
            'iso_utc': start_time_utc.isoformat(),
            'human_readable': start_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
        },
        'end_time': {
            'milliseconds': end_time_ms,
            'seconds': end_time_seconds,
            'iso_utc': end_time_utc.isoformat(),
            'human_readable': end_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
        },
        'duration': {
            'hours': 24,
            'milliseconds': end_time_ms - start_time_ms,
            'seconds': end_time_seconds - start_time_seconds
        }
    }
    
    return json.dumps(result, indent=2)