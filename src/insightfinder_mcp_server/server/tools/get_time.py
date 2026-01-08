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
def verify_13_digit_timestamp(timestamp: int) -> str:
    """Verify and convert a 13-digit timestamp (milliseconds) to human-readable format.
    
    ⚠️ CRITICAL: Always use this tool to verify timestamps before passing them to other tools!
    
    This tool validates that a timestamp is:
    1. A 13-digit number (milliseconds since Unix epoch)
    2. Within a reasonable range (year 1970-2286)
    3. Converts it to human-readable UTC format
    
    Args:
        timestamp: A 13-digit timestamp in milliseconds (e.g., 1767830400000)
    
    Returns:
        JSON with validation status, human-readable datetime, and timestamp details.
        If invalid, returns error message with suggested fixes.
    
    Example:
        Input: 1767830400000
        Output: {
            "valid": true,
            "timestamp_ms": 1767830400000,
            "datetime_utc": "2026-01-08 00:00:00 UTC",
            "iso_format": "2026-01-08T00:00:00+00:00",
            "date": "2026-01-08",
            "time": "00:00:00",
            "year": 2026,
            "month": 1,
            "day": 8,
            "hour": 0,
            "minute": 0,
            "second": 0
        }
    """
    try:
        # Validate timestamp is an integer
        if not isinstance(timestamp, int):
            try:
                timestamp = int(timestamp)
            except (ValueError, TypeError):
                return json.dumps({
                    'valid': False,
                    'error': 'Timestamp must be an integer',
                    'provided_value': str(timestamp),
                    'suggestion': 'Provide a 13-digit integer timestamp in milliseconds'
                }, indent=2)
        
        # Check if it's a 13-digit number
        timestamp_str = str(timestamp)
        if len(timestamp_str) != 13:
            suggestion = ""
            if len(timestamp_str) == 10:
                suggestion = f"This looks like a 10-digit timestamp (seconds). Convert to milliseconds: {timestamp * 1000}"
            elif len(timestamp_str) > 13:
                suggestion = f"Timestamp is too large. Divide by appropriate power of 10."
            else:
                suggestion = f"Timestamp is too small. This should be a 13-digit millisecond timestamp."
            
            return json.dumps({
                'valid': False,
                'error': f'Timestamp must be exactly 13 digits, got {len(timestamp_str)} digits',
                'provided_value': timestamp,
                'suggestion': suggestion
            }, indent=2)
        
        # Convert to datetime
        dt_utc = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        
        # Validate reasonable date range (1970-2286)
        if dt_utc.year < 1970 or dt_utc.year > 2286:
            return json.dumps({
                'valid': False,
                'error': f'Timestamp represents an unrealistic date: {dt_utc.year}',
                'provided_value': timestamp,
                'parsed_year': dt_utc.year,
                'suggestion': 'Check if timestamp is in milliseconds since Unix epoch (Jan 1, 1970)'
            }, indent=2)
        
        # Successfully validated - return detailed information
        result = {
            'valid': True,
            'timestamp_ms': timestamp,
            'datetime_utc': dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'iso_format': dt_utc.isoformat(),
            'date': dt_utc.strftime('%Y-%m-%d'),
            'time': dt_utc.strftime('%H:%M:%S'),
            'year': dt_utc.year,
            'month': dt_utc.month,
            'day': dt_utc.day,
            'hour': dt_utc.hour,
            'minute': dt_utc.minute,
            'second': dt_utc.second,
            'weekday': dt_utc.strftime('%A'),
            'timezone': 'UTC'
        }
        
        return json.dumps(result, indent=2)
        
    except (ValueError, OSError) as e:
        return json.dumps({
            'valid': False,
            'error': f'Failed to parse timestamp: {str(e)}',
            'provided_value': timestamp,
            'suggestion': 'Ensure timestamp is a valid 13-digit millisecond timestamp since Unix epoch'
        }, indent=2)

@mcp_server.tool()
def convert_iso8601_to_timestamp(iso_timestamp: str) -> str:
    """Convert an ISO 8601 timestamp to 13-digit milliseconds timestamp.
    
    This tool accepts various ISO 8601 formats and converts them to the 13-digit
    millisecond timestamp format required by the incident tools.
    
    Args:
        iso_timestamp: ISO 8601 formatted timestamp. Supported formats:
                      - "2026-01-08T21:45:30Z" (UTC with Z suffix)
                      - "2026-01-08T21:45:30+00:00" (UTC with explicit offset)
                      - "2026-01-08T21:45:30" (assumed UTC if no timezone)
                      - "2026-01-08 21:45:30" (space separator, assumed UTC)
                      - With milliseconds: "2026-01-08T21:45:30.123Z"
    
    Returns:
        JSON with the converted timestamp in milliseconds and human-readable formats.
    
    Example:
        Input: "2026-01-08T21:45:30Z"
        Output: {
            "valid": true,
            "input": "2026-01-08T21:45:30Z",
            "timestamp_ms": 1736372730000,
            "datetime_utc": "2026-01-08 21:45:30 UTC",
            "iso_format": "2026-01-08T21:45:30+00:00",
            "date": "2026-01-08",
            "time": "21:45:30",
            "year": 2026,
            "month": 1,
            "day": 8,
            "hour": 21,
            "minute": 45,
            "second": 30
        }
    """
    try:
        if not isinstance(iso_timestamp, str):
            return json.dumps({
                'valid': False,
                'error': 'Input must be a string',
                'provided_value': str(iso_timestamp),
                'suggestion': 'Provide an ISO 8601 formatted timestamp string (e.g., "2026-01-08T21:45:30Z")'
            }, indent=2)
        
        iso_timestamp = iso_timestamp.strip()
        
        if not iso_timestamp:
            return json.dumps({
                'valid': False,
                'error': 'Input cannot be empty',
                'suggestion': 'Provide an ISO 8601 formatted timestamp string (e.g., "2026-01-08T21:45:30Z")'
            }, indent=2)
        
        # Try to parse the ISO 8601 timestamp
        dt = None
        parse_error = None
        
        try:
            # Handle 'Z' suffix (UTC indicator)
            if iso_timestamp.endswith('Z'):
                iso_timestamp_parsed = iso_timestamp[:-1] + '+00:00'
            else:
                iso_timestamp_parsed = iso_timestamp
            
            # Handle space separator (replace with T)
            if ' ' in iso_timestamp_parsed and 'T' not in iso_timestamp_parsed:
                iso_timestamp_parsed = iso_timestamp_parsed.replace(' ', 'T', 1)
            
            # Parse the timestamp
            dt = datetime.fromisoformat(iso_timestamp_parsed)
            
            # If no timezone info, assume UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            
        except ValueError as e:
            parse_error = str(e)
        
        if dt is None:
            return json.dumps({
                'valid': False,
                'error': f'Failed to parse ISO 8601 timestamp: {parse_error}',
                'provided_value': iso_timestamp,
                'supported_formats': [
                    '2026-01-08T21:45:30Z',
                    '2026-01-08T21:45:30+00:00',
                    '2026-01-08T21:45:30',
                    '2026-01-08 21:45:30',
                    '2026-01-08T21:45:30.123Z'
                ],
                'suggestion': 'Ensure the timestamp follows ISO 8601 format'
            }, indent=2)
        
        # Convert to UTC if not already
        dt_utc = dt.astimezone(timezone.utc)
        
        # Convert to milliseconds timestamp
        timestamp_ms = int(dt_utc.timestamp() * 1000)
        timestamp_seconds = int(dt_utc.timestamp())
        
        # Validate reasonable date range (1970-2286)
        if dt_utc.year < 1970 or dt_utc.year > 2286:
            return json.dumps({
                'valid': False,
                'error': f'Timestamp represents an unrealistic date: {dt_utc.year}',
                'provided_value': iso_timestamp,
                'parsed_year': dt_utc.year,
                'suggestion': 'Check the year in your ISO 8601 timestamp'
            }, indent=2)
        
        # Successfully converted - return detailed information
        result = {
            'valid': True,
            'input': iso_timestamp,
            'timestamp_ms': timestamp_ms,
            'datetime_utc': dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'iso_format': dt_utc.isoformat(),
            'date': dt_utc.strftime('%Y-%m-%d'),
            'time': dt_utc.strftime('%H:%M:%S'),
            'year': dt_utc.year,
            'month': dt_utc.month,
            'day': dt_utc.day,
            'hour': dt_utc.hour,
            'minute': dt_utc.minute,
            'second': dt_utc.second,
            'weekday': dt_utc.strftime('%A'),
            'timezone': 'UTC'
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({
            'valid': False,
            'error': f'Unexpected error: {str(e)}',
            'provided_value': iso_timestamp,
            'suggestion': 'Ensure the timestamp follows ISO 8601 format (e.g., "2026-01-08T21:45:30Z")'
        }, indent=2)

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
    # start_time_user_tz = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
    start_time_user_tz = start_day
    
    # Calculate end time: end of current day (23:59:59.999)
    # end_time_user_tz = now_user_tz.replace(hour=23, minute=59, second=59, microsecond=999000)
    end_time_user_tz = now_user_tz
    
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
                   - "2026-08-21" (ISO format)
                   - "2026-08-21T10:30:00" (ISO datetime - time ignored)
                   - "08/21/2026" or "8/21/2026" (US format MM/DD/YYYY)
                   - "Aug 21, 2026" or "August 21, 2026" (US written format)
    
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
        
        # 1. ISO format: 2026-08-21 or 2026-08-21T10:30:00 (ignore year, use current)
        iso_match = re.match(r'^(\d{4})-(\d{2})-(\d{2})', date_input)
        if iso_match:
            _, month, day = map(int, iso_match.groups())  # Ignore year
            target_date = datetime(current_year, month, day).date()
        
        # 2. US format: MM/DD/YYYY (08/21/2026 or 8/21/2026) (ignore year, use current)
        elif re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', date_input):
            us_match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_input)
            if us_match:
                month, day, _ = map(int, us_match.groups())  # Ignore year
                target_date = datetime(current_year, month, day).date()
        
        # 3. US written format: Aug 21, 2026 or August 21, 2026 (ignore year, use current)
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
                    "2026-08-21 (ISO format)",
                    "2026-08-21T10:30:00 (ISO datetime)",
                    "08/21/2026 (US format MM/DD/YYYY)", 
                    "8/21/2026 (US format M/D/YYYY)",
                    "Aug 21, 2026 (US written format)",
                    "August 21, 2026 (US written format)"
                ],
                'parse_error': str(e)
            }, indent=2)
    
    # Check if we successfully parsed a date
    if target_date is None:
        return json.dumps({
            'error': f"Failed to parse date: {date_input}",
            'supported_formats': [
                "2026-08-21 (ISO format)",
                "2026-08-21T10:30:00 (ISO datetime)",
                "08/21/2026 (US format MM/DD/YYYY)", 
                "8/21/2026 (US format M/D/YYYY)",
                "Aug 21, 2026 (US written format)",
                "August 21, 2026 (US written format)"
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