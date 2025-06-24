# In your MCP server (insightfinder_mcp_server)
from datetime import datetime, timezone, timedelta
from ..server import mcp_server

import zoneinfo
import json

@mcp_server.tool()
def get_current_datetime() -> str:
    """Get the current date and time in various formats.
    
    Returns current UTC time, local time, and common timezone information
    that can be used for time-based queries and filtering.
    """
    now_utc = datetime.now(timezone.utc)
    
    # Get common timezone times
    timezones = {
        'UTC': now_utc,
        'America/New_York': now_utc.astimezone(zoneinfo.ZoneInfo('America/New_York')),
        'America/Los_Angeles': now_utc.astimezone(zoneinfo.ZoneInfo('America/Los_Angeles')),
    }
    
    result = {
        'current_utc': now_utc.isoformat(),
        'current_utc_timestamp': int(now_utc.timestamp()),
        'formatted_times': {}
    }
    
    for tz_name, tz_time in timezones.items():
        result['formatted_times'][tz_name] = {
            'iso_format': tz_time.isoformat(),
            'human_readable': tz_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'date_only': tz_time.strftime('%Y-%m-%d'),
            'time_only': tz_time.strftime('%H:%M:%S'),
            'timestamp': int(tz_time.timestamp())
        }
    
    # Add relative time helpers
    result['relative_times'] = {
        '1_hour_ago': int((now_utc - timedelta(hours=1)).timestamp()),
        '30_minutes_ago': int((now_utc - timedelta(minutes=30)).timestamp()),
        '24_hours_ago': int((now_utc - timedelta(hours=24)).timestamp()),
        '1_week_ago': int((now_utc - timedelta(days=7)).timestamp())
    }
    
    return json.dumps(result, indent=2)